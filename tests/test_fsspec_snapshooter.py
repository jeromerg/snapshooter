import hashlib
import os
from datetime import datetime
from gzip import GzipFile
from pathlib import Path

import fsspec
import pytest

from snapshooter import Snapshooter, jsonl_utils
from snapshooter.snapshooter import Heap

this_file_dir = os.path.dirname(os.path.abspath(__file__))


def get_file_md5(file_path: str) -> str:
    with open(file_path, "rb") as f:
        return hashlib.md5(f.read()).hexdigest()


@pytest.fixture
def data_root():
    r = f"{this_file_dir}/temp/{datetime.now().strftime('%Y%m%d_%H%M%S_%f')}"
    print(f"test_data_root={r}")
    return r


def test_main_functionalities(data_root):
    heap = Heap(
        heap_fs=fsspec.filesystem("file"),
        heap_root=f"{data_root}/heap",
    )
    snapshooter = Snapshooter(
        src_fs    = fsspec.filesystem("file"),
        src_root  = f"{this_file_dir}/unit_test_data/sample_src",
        snap_fs   = fsspec.filesystem("file"),
        snap_root = f"{data_root}/snap",
        heap      = heap,
    )
    snap, timestamp = snapshooter.make_snapshot()
    assert timestamp is not None
    assert snap is not None
    assert isinstance(timestamp, datetime)
    assert isinstance(snap, list)
    assert len(snap) == 3
    # result is expected to be sorted by name (relative path to root)
    assert snap[0]["name"] == "empty_file.txt"
    assert snap[1]["name"] == "subfolder/another_text_file.txt"
    assert snap[2]["name"] == "text_file.txt"
    assert snap[0]["md5" ] == "b6f750f20a040a360774725bae513f17"
    assert snap[1]["md5" ] == "d41d8cd98f00b204e9800998ecf8427e"
    assert snap[2]["md5" ] == "41060d3ddfdf63e68fc2bf196f652ee9"
    
    snapshot_path = snapshooter._save_snapshot(snap, timestamp)
    
    print(f"snapshot_path={snapshot_path}")
    
    os.path.isfile(snapshot_path)
    
    with open(snapshot_path, "rb") as f, GzipFile(fileobj=f) as gzip_file:
        reloaded_snap = jsonl_utils.loads_jsonl(gzip_file.read().decode("utf-8"))
    
    assert reloaded_snap == snap

    # -------------------------------
    heap = Heap(
        heap_fs   = fsspec.filesystem("file"),
        heap_root = f"{data_root}/heap",
    )
    restore_snapshooter = Snapshooter(
        src_fs    = fsspec.filesystem("file"),
        src_root  = f"{data_root}/restored",
        snap_fs   = fsspec.filesystem("file"),
        snap_root = f"{data_root}/snap",
        heap      = heap,
    )

    restore_snapshooter.restore_snapshot()
    
    restored_root = f"{data_root}/restored"
    ls = [str(f.relative_to(restored_root)) for f in Path(restored_root).rglob('*') if f.is_file()]
    ls = [f.replace("\\", "/") for f in ls]
    ls = sorted(ls)
    assert len(ls) == 3
    assert ls[0] == "empty_file.txt"
    assert ls[1] == "subfolder/another_text_file.txt"
    assert ls[2] == "text_file.txt"
    assert get_file_md5(f"{data_root}/restored/{ls[0]}") == "b6f750f20a040a360774725bae513f17"
    assert get_file_md5(f"{data_root}/restored/{ls[1]}") == "d41d8cd98f00b204e9800998ecf8427e"
    assert get_file_md5(f"{data_root}/restored/{ls[2]}") == "41060d3ddfdf63e68fc2bf196f652ee9"
