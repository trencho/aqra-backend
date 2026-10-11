from abc import ABC, abstractmethod
from logging import getLogger
from uuid import uuid4

from bson.objectid import ObjectId
from pymongo import ASCENDING, MongoClient, UpdateOne
from pymongo.errors import OperationFailure

from definitions import CHUNK_SIZE, COLLECTIONS
from settings import get_settings

logger = getLogger(__name__)


class Repository(ABC):
    @abstractmethod
    def get(self, collection_name, filter=None, **kwargs):
        pass

    @abstractmethod
    def get_many(self, collection_name, filter=None, **kwargs) -> list:
        pass

    @abstractmethod
    def save(self, collection_name, filter, item) -> None:
        pass

    @abstractmethod
    def save_many(self, collection_name, items) -> None:
        pass

    @abstractmethod
    def delete(self, collection_name, item) -> None:
        pass

    @abstractmethod
    def insert_missing(self, collection_name, items: list, keys: tuple) -> int:
        """Insert each item whose `keys` are not stored yet; never overwrite. Returns the count.

        Insert-only on purpose: it is what the old find-what-is-missing pass did, so an hour
        OpenWeather revises in a later fetch keeps its first value, exactly as before.
        """

    @abstractmethod
    def count(self, collection_name, filter=None) -> int:
        """How many documents match, without loading them."""

    @abstractmethod
    def delete_duplicates(self, collection_name, keys: tuple) -> int:
        """Keep one document per `keys` value, delete the rest. Returns how many went."""

    def ensure_indexes(self) -> None:
        """Create the indexes queries rely on. Nothing to do for a store without indexes."""


class RegularRepository(Repository):
    def __init__(self, uri):
        self.mongo_client = MongoClient(host=uri, connect=False)
        self.database = self.mongo_client.get_default_database()

    def ensure_indexes(self) -> None:
        # Explicit, not in __init__. Creating indexes in the constructor meant importing any module
        # that resolved the repository at import time sent commands to Mongo before the app existed.
        self.database["cities"].create_index([("cityName", ASCENDING)])
        self.database["countries"].create_index([("countryCode", ASCENDING)])
        self.database["sensors"].create_index([("sensorId", ASCENDING)])
        self.database["predictions"].create_index(
            [("cityName", ASCENDING), ("sensorId", ASCENDING)]
        )
        for collection in COLLECTIONS:
            self.database[collection].create_index([("sensorId", ASCENDING)])
            # One reading per sensor per hour. Older code could store an hour twice, and a
            # collection holding duplicates cannot take a unique index, so log instead of
            # stopping the app; the migration's dedupe lets the next start create it.
            try:
                self.database[collection].create_index(
                    [("sensorId", ASCENDING), ("time", ASCENDING)],
                    unique=True,
                    name="sensorId_time_unique",
                )
            except OperationFailure:
                logger.exception(
                    "%s holds duplicate readings, so the unique (sensorId, time) index was "
                    "not created; run `python -m worker.migrate` to remove them",
                    collection,
                )

    def get(self, collection_name, filter=None, **kwargs):
        collection = self.database[collection_name]
        document = collection.find_one(filter=filter, **kwargs)
        return document if document else None

    def get_many(self, collection_name, filter=None, **kwargs) -> list:
        collection = self.database[collection_name]
        documents = collection.find(filter, **kwargs)
        return list(documents)

    def save(self, collection_name, filter, item) -> None:
        collection = self.database[collection_name]
        if filter is None:
            collection.insert_one(item if isinstance(item, dict) else item.__dict__)
        else:
            collection.replace_one(
                filter=filter,
                replacement=item if isinstance(item, dict) else item.__dict__,
                upsert=True,
            )

    def save_many(self, collection_name, items) -> None:
        collection = self.database[collection_name]
        documents = [
            item if isinstance(item, dict) else item.__dict__ for item in items
        ]
        results = collection.insert_many(documents)
        for item, result in zip(items, results.inserted_ids):
            if isinstance(item, dict):
                item["_id"] = str(result)
            else:
                item.id = str(result)

    def delete(self, collection_name, item) -> None:
        collection = self.database[collection_name]
        if item.id is not None:
            collection.delete_one({"_id": ObjectId(item.id)})

    def insert_missing(self, collection_name, items: list, keys: tuple) -> int:
        collection = self.database[collection_name]
        upserted = 0
        for start in range(0, len(items), CHUNK_SIZE):
            operations = [
                UpdateOne(
                    {key: item[key] for key in keys},
                    {"$setOnInsert": item},
                    upsert=True,
                )
                for item in items[start : start + CHUNK_SIZE]
            ]
            upserted += collection.bulk_write(operations, ordered=False).upserted_count
        return upserted

    def count(self, collection_name, filter=None) -> int:
        return self.database[collection_name].count_documents(filter or {})

    def delete_duplicates(self, collection_name, keys: tuple) -> int:
        collection = self.database[collection_name]
        groups = collection.aggregate(
            [
                {
                    "$group": {
                        "_id": {key: f"${key}" for key in keys},
                        "ids": {"$push": "$_id"},
                        "count": {"$sum": 1},
                    }
                },
                {"$match": {"count": {"$gt": 1}}},
            ],
            allowDiskUse=True,
        )
        deleted = 0
        for group in groups:
            deleted += collection.delete_many(
                {"_id": {"$in": group["ids"][1:]}}
            ).deleted_count
        return deleted


class InMemoryRepository(Repository):
    def __init__(self):
        self.collections = {}

    def get(self, collection_name, filter=None, **kwargs):
        collection = self.collections.get(collection_name, {})
        if filter:
            for item in collection.values():
                if self._matches_filter(item, filter):
                    return item
        return None

    def get_many(self, collection_name, filter=None, **kwargs) -> list:
        collection = self.collections.get(collection_name, {})
        if filter:
            return [
                item
                for item in collection.values()
                if self._matches_filter(item, filter)
            ]
        return list(collection.values())

    def save(self, collection_name, filter, item) -> None:
        collection = self.collections.setdefault(collection_name, {})
        if isinstance(item, dict):
            self._save_dict_item(collection, filter, item)
        else:
            self._save_class_item(collection, filter, item)

    @staticmethod
    def _save_dict_item(collection, filter, item):
        if filter is None:
            item_id = item.get("_id") or str(uuid4())
            item["_id"] = item_id
            collection[item_id] = item
        else:
            existing_item = next(
                (
                    existing_item
                    for existing_item in collection.values()
                    if all(existing_item.get(k) == v for k, v in filter.items())
                ),
                None,
            )
            if existing_item:
                existing_item.update(item)
            else:
                item_id = item.get("_id") or str(uuid4())
                item["_id"] = item_id
                collection[item_id] = item

    @staticmethod
    def _save_class_item(collection, filter, item):
        if filter is None:
            item_id = item.id or str(uuid4())
            item.id = item_id
            collection[item_id] = item
        else:
            existing_item = next(
                (
                    existing_item
                    for existing_item in collection.values()
                    if all(
                        existing_item.__dict__.get(k) == v for k, v in filter.items()
                    )
                ),
                None,
            )
            if existing_item:
                existing_item.__dict__.update(item.__dict__)
            else:
                item_id = item.id or str(uuid4())
                item.id = item_id
                collection[item_id] = item

    def save_many(self, collection_name, items) -> None:
        for item in items:
            self.save(collection_name=collection_name, filter=None, item=item)

    def delete(self, collection_name, item) -> None:
        collection = self.collections.get(collection_name, {})
        if isinstance(item, dict):
            if item["_id"] in collection:
                del collection[item["_id"]]
        else:
            if item.id in collection:
                del collection[item.id]

    def insert_missing(self, collection_name, items: list, keys: tuple) -> int:
        collection = self.collections.setdefault(collection_name, {})
        index = {
            tuple(existing.get(key) for key in keys): existing
            for existing in collection.values()
            if isinstance(existing, dict)
        }
        upserted = 0
        for item in items:
            identity = tuple(item[key] for key in keys)
            if identity in index:
                continue
            stored = dict(item)
            stored["_id"] = str(uuid4())
            collection[stored["_id"]] = stored
            index[identity] = stored
            upserted += 1
        return upserted

    def count(self, collection_name, filter=None) -> int:
        return len(self.get_many(collection_name, filter))

    def delete_duplicates(self, collection_name, keys: tuple) -> int:
        collection = self.collections.get(collection_name, {})
        seen, duplicates = set(), []
        for item_id, item in collection.items():
            identity = tuple(item.get(key) for key in keys)
            if identity in seen:
                duplicates.append(item_id)
            else:
                seen.add(identity)
        for item_id in duplicates:
            del collection[item_id]
        return len(duplicates)

    @staticmethod
    def _matches_filter(item, filter) -> bool:
        # Items are stored as dicts (every collection) or as objects; mirror ``get``'s
        # dict access instead of ``__dict__``, which a plain ``dict`` doesn't have.
        data = item if isinstance(item, dict) else item.__dict__
        return all(data.get(key) == value for key, value in filter.items())


class RepositorySingleton:
    _instance = None
    _repository = None

    @staticmethod
    def get_instance():
        if not RepositorySingleton._instance:
            RepositorySingleton._instance = RepositorySingleton()
        return RepositorySingleton._instance

    def __init__(self):
        if not RepositorySingleton._repository:
            settings = get_settings()
            if settings.is_production:
                RepositorySingleton._repository = RegularRepository(settings.mongo_uri)
            else:
                RepositorySingleton._repository = InMemoryRepository()

    @staticmethod
    def get_repository() -> Repository:
        return RepositorySingleton._repository


def get_repository() -> Repository:
    """The process-wide repository, created on first use rather than at import."""
    return RepositorySingleton.get_instance().get_repository()
