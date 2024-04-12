import base64
import datetime
import re
from typing import Callable, Dict
from fsspec import AbstractFileSystem

from snapshooter.logging_utils import log_str


# try_find_md5_in_file_info_azure_fs(file_info: dict, bak_latest_snapshot:Dict[str, dict]) -> str|None
Md5Getter = Callable[[dict, Dict[str, dict]], str|None]  

REGEX_DIGITS_SPLITTER = re.compile(r"(\d+)")


def improved_AbstractFileSystem_str_function(self: AbstractFileSystem, visited: set = None) -> str:
    if visited is None:
        visited = set()
    visited.add(self)

    name = f"{self.__class__.__name__}"
    kvs = []
    for key, value in self.__dict__.items():        
        if key.startswith("_"):
            continue
        if isinstance(value, (int, float, bool)):
            kvs.append(f"{key}={value}")
        elif isinstance(value, str):
            kvs.append(f"{key}='{log_str(value, length=30)}'")
        elif isinstance(value, AbstractFileSystem):
            if value in visited:
                kvs.append(f"{key}={value.__class__.__name__}(already described)")
            else:
                kvs.append(f"{key}={improved_AbstractFileSystem_str_function(value, visited)}")
        else:
            pass
            #kvs.append(f"{key}={log_str(str(value), length=30)}")
    return log_str(f"{name}({', '.join(kvs)})", length=300)


def patch_AbstractFileSystem_str_function():
    AbstractFileSystem.__str__ = improved_AbstractFileSystem_str_function


def natural_sort_key(s: str) -> list[str | int]:
    return [int(part) if part.isdigit() else part for part in REGEX_DIGITS_SPLITTER.split(s)]


def jsonify_file_info(node, debug_path:list=None):
    if debug_path is None:
        debug_path = []
    if isinstance(node, dict) or "items" in dir(node):
        return {k: jsonify_file_info(v, [*debug_path, k]) for k, v in node.items()}
    elif isinstance(node, list):
        return [jsonify_file_info(v, [*debug_path, i]) for i, v in enumerate(node)]
    elif isinstance(node, datetime.datetime):
        return node.isoformat()
    elif isinstance(node, (bytearray, bytes)):
        return base64.b64encode(node).decode("utf-8")
    elif isinstance(node, (int, float, str, bool, type(None))):
        return node
    else:
        raise Exception(f"Unknown type {type(node)} for {debug_path}")


def try_find_md5_in_file_info_azure_fs(file_info: dict, latest_snapshot_by_filename:Dict[str, dict]) -> str|None:
    file_name = file_info["name"]
    if (
        "content_settings" in file_info
        and "content_md5" in file_info["content_settings"]
        and file_info["content_settings"]["content_md5"] is not None
    ):
        md5_base64 = file_info["content_settings"]["content_md5"]
        md5_bytes = base64.b64decode(md5_base64)
        md5 = md5_bytes.hex()
        return md5

    if (
        "etag" in file_info
        and file_info["etag"] is not None
        and file_name in latest_snapshot_by_filename
        and "etag" in latest_snapshot_by_filename[file_name]
        and latest_snapshot_by_filename[file_name]["etag"] == file_info["etag"]
        and latest_snapshot_by_filename[file_name].get("md5", None) is not None
    ):
        return latest_snapshot_by_filename[file_name]["md5"]

    return None


def try_find_md5_hex_in_file_info_local_fs(file_info: dict, latest_snapshot_by_filename:Dict[str, dict]) -> str|None:
    file_name = file_info["name"]
    if (
        "mtime" in file_info
        and file_info["mtime"] is not None
        and file_name in latest_snapshot_by_filename
        and "mtime" in latest_snapshot_by_filename[file_name]
        and latest_snapshot_by_filename[file_name]["mtime"] == file_info["mtime"]
        and latest_snapshot_by_filename[file_name].get("md5", None) is not None
    ):
        return latest_snapshot_by_filename[file_name]["md5"]

    return None


md5_getter_by_fs_protocol : Md5Getter = {
    "az": try_find_md5_in_file_info_azure_fs,
    "adl": try_find_md5_in_file_info_azure_fs,
    "abfs": try_find_md5_in_file_info_azure_fs,
    "local": try_find_md5_hex_in_file_info_local_fs,
    "file": try_find_md5_hex_in_file_info_local_fs,
}


def get_md5_getter(src_fs: AbstractFileSystem) -> Md5Getter:
    protocol = src_fs.protocol
    if not isinstance(protocol, tuple):
        protocol = (protocol,)

    for p in protocol:
        md5_getter = md5_getter_by_fs_protocol.get(p, None)
        if md5_getter is not None:
            return md5_getter
    raise Exception(f"Unsupported fs_protocol(s) {src_fs.protocol}: No md5 getter implemented")
