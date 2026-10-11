"""Maintenance job: back the collected data up to a private GitHub repository."""

from base64 import b64encode
from datetime import datetime
from os import walk
from pathlib import Path

from backup import append_commit_files, create_archive, update_git_files
from definitions import DATA_PATH
from settings import get_settings
from utils import track_time


@track_time
def dump_data() -> None:
    file_list, file_names = [], []
    for root, directories, files in walk(DATA_PATH):
        if not directories and files:
            file_path = Path(f"{root}.zip")
            create_archive(source=Path(root), destination=file_path)
            data = b64encode(file_path.read_bytes())
            append_commit_files(
                file_list,
                data,
                Path(root).resolve().parent,
                Path(file_path).name,
                file_names,
            )
            file_path.unlink(missing_ok=True)

    if file_list:
        update_git_files(
            file_list,
            file_names,
            get_settings().repo_name,
            "master",
            f"Scheduled data dump - {datetime.now().strftime('%H:%M:%S %d-%m-%Y')}",
        )
