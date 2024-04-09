import json
import logging
from dataclasses import dataclass
from datetime import datetime

import fsspec
import typer
from fsspec import AbstractFileSystem
from typing_extensions import Annotated

from snapshooter import Heap, Snapshooter

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


main_cli = typer.Typer()


@dataclass
class SharedConfig:
    root_dir             : str
    root_storage_options : str
    heap_dir             : str
    heap_storage_options : str
    snap_dir             : str
    snap_storage_options : str


@main_cli.callback(no_args_is_help=True)
def shared_to_all_commands(
    ctx                  : typer.Context,
    file_root            : Annotated[str, typer.Option(envvar="FILE_ROOT"             , help="The directory under consideration, to backup or to restore to. Provided as fsspec path/uri")],
    file_storage_options : Annotated[str, typer.Option(envvar="FILE_STORAGE_OPTIONS"  , help="Additional storage options to pass to fsspec dir file system. expected JSON string")],
    heap_root            : Annotated[str, typer.Option(envvar="HEAP_ROOT"            , help="The directory containing the heap files. Provided as fsspec path/uri")],
    heap_storage_options : Annotated[str, typer.Option(envvar="HEAP_STORAGE_OPTIONS" , help="Additional storage options to pass to fsspec heap_dir file system. expected JSON string")],
    snap_root            : Annotated[str, typer.Option(envvar="SNAP_ROOT"            , help="The directory containing the snapshot files. Provided as fsspec path/uri")],
    snap_storage_options : Annotated[str, typer.Option(envvar="SNAP_STORAGE_OPTIONS" , help="Additional storage options to pass to fsspec snap_dir file system. expected JSON string")],
):
    file_storage_options_dict  = json.loads(file_storage_options)
    heap_storage_options_dict = json.loads(heap_storage_options)
    snap_storage_options_dict = json.loads(snap_storage_options)

    file_fs : AbstractFileSystem = fsspec.url_to_fs(file_root, **file_storage_options_dict)
    heap_fs : AbstractFileSystem = fsspec.url_to_fs(heap_root, **heap_storage_options_dict)
    snap_fs : AbstractFileSystem = fsspec.url_to_fs(snap_root, **snap_storage_options_dict)

    heap = Heap(heap_fs=heap_fs, heap_root=f"{heap_root}/heap")
    snapshooter = Snapshooter(file_fs=file_fs, file_root=file_root, snap_fs=snap_fs, snap_root=snap_root, heap=heap)

    ctx.obj = snapshooter
    ctx.ensure_object(Snapshooter)


@main_cli.command()
def make_snapshot(
    ctx: typer.Context,
    save_snapshot: Annotated[bool, typer.Option(help="Whether to save the snapshot or not. If False, the snapshot is not saved, but the snapshot is returned as a list of dictionaries. Default is True.")] = True,
    download_missing_files: Annotated[bool, typer.Option(help="Whether to download missing files or not. If True, missing files are downloaded. Remark: files with unknown md5 will still be required to be downloaded. Default is True.")] = True,
):
    snapshooter: Snapshooter = ctx.obj
    snapshooter.make_snapshot(
        save_snapshot=save_snapshot,
        download_missing_files=download_missing_files
    )


@main_cli.command()
def restore_snapshot(
    ctx: typer.Context,
    latest: Annotated[str, typer.Argument(help="If set, then look for the latest snapshot before or at this timestamp. Expected format is 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS[offset]'.")],
):
    snapshooter: Snapshooter = ctx.obj
    latest_timestamp = datetime.fromisoformat(latest)
    snapshooter.restore_snapshot(latest_timestamp=latest_timestamp)


if __name__ == '__main__':
    main_cli()
