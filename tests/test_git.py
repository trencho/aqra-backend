"""Regression test for ``src/backup.merge_csv_files``.

The bug (A1): ``merge_csv_files`` used to feed ``read_csv_in_chunks`` the *value* of the
buffers (``StringIO.getvalue()`` / ``Path(bytes)``) instead of the buffer objects, so
every merge raised inside the ``try`` and the function returned ``None``. The fix passes
the ``StringIO``/``BytesIO`` buffers through unchanged. This test drives the function
with a plain CSV string plus a fake repo whose ``get_contents`` returns CSV bytes and
asserts a non-``None`` merged CSV comes back.
"""

from unittest.mock import Mock

from backup import merge_csv_files


def test_merge_csv_files_returns_merged_csv_from_string_and_repo_bytes():
    local_csv = "time,pm2_5\n1704067200,12.0\n1704070800,13.0\n"
    repo_bytes = b"time,pm2_5\n1704074400,15.0\n"

    repo = Mock()
    repo.get_contents.return_value.decoded_content = repo_bytes

    result = merge_csv_files(repo, "skopje/1000/pollution.csv", local_csv)

    repo.get_contents.assert_called_once_with("skopje/1000/pollution.csv")
    # Before the fix this returned None; now it is a merged CSV string.
    assert result is not None
    assert isinstance(result, str)
    assert "pm2_5" in result
    # Rows from both the local string and the repo bytes survive the merge.
    assert "12.0" in result
    assert "15.0" in result
