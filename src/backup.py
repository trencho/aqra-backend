from datetime import datetime
from io import BytesIO, StringIO
from logging import getLogger
from pathlib import Path
from shutil import make_archive, move

from github import (
    Github,
    GithubException,
    GitRef,
    GitTree,
    InputGitTreeElement,
    Repository,
)
from github.Auth import Token
from pandas import concat
from requests import ReadTimeout
from requests.exceptions import ChunkedEncodingError
from urllib3.exceptions import ReadTimeoutError

from definitions import ROOT_PATH
from processing import read_csv_in_chunks, trim_dataframe
from settings import get_settings

logger = getLogger(__name__)


class GithubSingleton:
    _instance = None

    @staticmethod
    def get_instance():
        if not GithubSingleton._instance:
            GithubSingleton._instance = GithubSingleton()
        return GithubSingleton._instance

    def __init__(self):
        token = get_settings().github_token
        self.github_instance = Github(auth=Token(token)) if token else None

    def get_repository(self, repo_name: str) -> Repository:
        return self.github_instance.get_user().get_repo(repo_name)


def append_commit_files(
    file_list: list, data: bytes | str, root: Path, file: str, file_names: list
) -> None:
    file_list.append(data)
    rel_file = (root.relative_to(ROOT_PATH) / file).as_posix().lstrip("./")
    file_names.append(rel_file)


def commit_git_files(
    repo: Repository,
    master_ref: GitRef,
    master_sha: str,
    base_tree: GitTree,
    commit_message: str,
    element_list: list,
) -> None:
    try:
        tree = repo.create_git_tree(element_list, base_tree)
        parent = repo.get_git_commit(master_sha)
        commit = repo.create_git_commit(commit_message, tree, [parent])
        master_ref.edit(commit.sha)
    except (ChunkedEncodingError, GithubException, ReadTimeout, ReadTimeoutError):
        if len(element_list) // 2 > 0:
            commit_git_files(
                repo,
                master_ref,
                master_sha,
                base_tree,
                commit_message,
                element_list[: len(element_list) // 2],
            )
            commit_git_files(
                repo,
                master_ref,
                master_sha,
                base_tree,
                commit_message,
                element_list[len(element_list) // 2 :],
            )
        logger.exception(
            "Error occurred while committing files to GitHub",
        )


def create_archive(source: Path, destination: Path) -> None:
    name, fmt = destination.name.rsplit(".", 1)
    archive_from = source.parent
    archive_to = source.name

    archive_file = make_archive(
        base_name=str(archive_from / name),
        format=fmt,
        root_dir=archive_from,
        base_dir=archive_to,
    )
    move(archive_file, destination)


def merge_csv_files(repo: Repository, file_name: str, data: str) -> str | None:
    try:
        with StringIO(data) as string_io_data:
            local_file_content = read_csv_in_chunks(string_io_data)
        repo_file = repo.get_contents(file_name)
        with BytesIO(repo_file.decoded_content) as bytes_io_data:
            repo_file_content = read_csv_in_chunks(bytes_io_data)
        combined_content = concat(
            [local_file_content, repo_file_content], ignore_index=True
        )
        combined_content = trim_dataframe(combined_content, "time")
        return combined_content.to_csv(index=False)
    except Exception:
        logger.exception(
            "Error occurred while merging local files with files from GitHub repository",
        )
        return None


def update_git_files(
    file_list: list,
    file_names: list,
    repo_name: str,
    branch: str,
    commit_message: str = None,
) -> None:
    if commit_message is None:
        commit_message = (
            f"Data Updated - {datetime.now().strftime('%H:%M:%S %d-%m-%Y')}"
        )
    repo = GithubSingleton.get_instance().get_repository(repo_name)
    master_ref = repo.get_git_ref(f"heads/{branch}")
    master_sha = master_ref.object.sha
    base_tree = repo.get_git_tree(master_sha)
    element_list = []
    element = None
    for i, file_name in enumerate(file_names):
        if file_name.endswith(".csv"):
            file = merge_csv_files(repo, file_name, file_list[i])
            element = InputGitTreeElement(file_name, "100644", "blob", file)
        elif file_name.endswith((".png", ".zip")):
            file = repo.create_git_blob(file_list[i].decode(), "base64")
            element = InputGitTreeElement(file_name, "100644", "blob", sha=file.sha)
        element_list.append(element)

    commit_git_files(
        repo, master_ref, master_sha, base_tree, commit_message, element_list
    )
    logger.info(f"Files committed to GitHub repository: {repo_name}")
