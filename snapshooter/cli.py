import json
import logging
import sys
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import List, Literal
import colorlog

import fsspec
import typer
from typing_extensions import Annotated
from snapshooter import Heap, Snapshooter, convert_snapshot_to_df, compare_snapshots as compare_snapshots_
from snapshooter.fsspec_utils import natural_sort_key


class LogLevel(str, Enum):
    CRITICAL = "CRITICAL"
    ERROR    = "ERROR"
    WARNING  = "WARNING"
    INFO     = "INFO"
    DEBUG    = "DEBUG"
    NONE     = "NONE"
    

def setup_logging(loglevel: LogLevel = "INFO"):    
    if loglevel == "NONE":
        return
    
    loglevel_value = getattr(logging, loglevel.upper())
    
    formatter = colorlog.ColoredFormatter(
        '%(log_color)s%(asctime)s %(levelname)-8s %(name)s - %(message)s', 
        datefmt="%H:%M:%S",
        stream=sys.stderr
    )
    handler = logging.StreamHandler()
    logger = logging.getLogger()
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(loglevel_value)
    handler.setLevel(loglevel_value)

    # shift logging for azure.core.pipeline.policies.http_logging_policy (if root logger is set to INFO, then set this to WARNING and so one)
    logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(loglevel_value + 10)


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
    ctx                     : typer.Context,
    file_root               : Annotated[str, typer.Option(envvar="FILE_ROOT"               , help="The directory under consideration, to backup or to restore to. Provided as fsspec path/uri")],
    heap_root               : Annotated[str, typer.Option(envvar="HEAP_ROOT"               , help="The directory containing the heap files. Provided as fsspec path/uri")],
    snap_root               : Annotated[str, typer.Option(envvar="SNAP_ROOT"               , help="The directory containing the snapshot files. Provided as fsspec path/uri")],
    file_storage_options    : Annotated[str, typer.Option(envvar="FILE_STORAGE_OPTIONS"    , help="Additional storage options to pass to fsspec dir file system. expected JSON string")] = None,
    heap_storage_options    : Annotated[str, typer.Option(envvar="HEAP_STORAGE_OPTIONS"    , help="Additional storage options to pass to fsspec heap_dir file system. expected JSON string")] = None,
    snap_storage_options    : Annotated[str, typer.Option(envvar="SNAP_STORAGE_OPTIONS"    , help="Additional storage options to pass to fsspec snap_dir file system. expected JSON string")] = None,
    parallel_copy_to_heap   : Annotated[int, typer.Option(envvar="PARALLEL_COPY_TO_HEAP"   , help="Number of parallel threads to use for copying files to heap")] = 20,
    parallel_copy_to_file   : Annotated[int, typer.Option(envvar="PARALLEL_COPY_TO_FILE"   , help="Number of parallel threads to use for copying files to file")] = 20,
    parallel_delete_in_file : Annotated[int, typer.Option(envvar="PARALLEL_DELETE_IN_FILE" , help="Number of parallel threads to use for deleting files in file")] = 20,
    parallel_listing        : Annotated[int, typer.Option(envvar="PARALLEL_LISTING"        , help="Number of parallel threads to use for listing files in file")] = 20,
    parallel_heap_listing   : Annotated[int, typer.Option(envvar="PARALLEL_HEAP_LISTING"   , help="Number of parallel threads to use for listing files in heap")] = 20,
    loglevel                : Annotated[LogLevel, typer.Option(envvar="LOGLEVEL"           , help="The log level to use. Default is INFO.")] = "INFO",
):
    setup_logging(loglevel)
    
    file_storage_options_dict = json.loads(file_storage_options or "{}")
    heap_storage_options_dict = json.loads(heap_storage_options or "{}")
    snap_storage_options_dict = json.loads(snap_storage_options or "{}")

    file_fs, file_root = fsspec.url_to_fs(file_root, **file_storage_options_dict)
    heap_fs, heap_root = fsspec.url_to_fs(heap_root, **heap_storage_options_dict)
    snap_fs, snap_root = fsspec.url_to_fs(snap_root, **snap_storage_options_dict)

    heap = Heap(
        heap_fs          = heap_fs, 
        heap_root        = heap_root, 
        parallel_listing = parallel_heap_listing
    )
    snapshooter = Snapshooter(
        file_fs                 = file_fs, 
        file_root               = file_root, 
        snap_fs                 = snap_fs, 
        snap_root               = snap_root, 
        heap                    = heap,
        parallel_copy_to_heap   = parallel_copy_to_heap,
        parallel_copy_to_file   = parallel_copy_to_file,
        parallel_delete_in_file = parallel_delete_in_file,
        parallel_listing        = parallel_listing,
    )

    ctx.obj = snapshooter
    ctx.ensure_object(Snapshooter)


@main_cli.command()
def make_snapshot(
    ctx                    : typer.Context,
    save_snapshot          : Annotated[bool, typer.Option(help="Whether to save the snapshot or not. If False, the snapshot is not saved, but the snapshot is returned as a list of dictionaries. Default is True.")] = True,
    download_missing_files : Annotated[bool, typer.Option(help="Whether to download missing files or not. If True, missing files are downloaded. Remark: files with unknown md5 will still be required to be downloaded. Default is True.")] = True,
):
    snapshooter: Snapshooter = ctx.obj
    snap, ts, path = snapshooter.make_snapshot(
        save_snapshot=save_snapshot,
        download_missing_files=download_missing_files
    )
    print(path)


@main_cli.command()
def restore_snapshot(
    ctx                  : typer.Context,
    path                 : Annotated[str, typer.Option(help="The path to the snapshot file to restore. If not set, then it will look for the latest snapshot available, that fulfills the --latest timestamp if provided")] = None,
    latest               : Annotated[str, typer.Option(help="If set, then look for the latest snapshot before or at this timestamp. Expected format is 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS[offset]'.")] = None,
    save_snapshot_before : Annotated[bool, typer.Option(help="Whether to save the current state into a 'backup' snapshot or not. Default is True.")] = True,
    save_snapshot_after  : Annotated[bool, typer.Option(help="Whether to save the restored state into a 'backup' snapshot or not. Default is True.")] = False,
):
    snapshooter: Snapshooter = ctx.obj
    latest_timestamp = datetime.fromisoformat(latest) if latest is not None else None
    snapshooter.restore_snapshot(
        snapshot_to_restore=path,
        latest_timestamp=latest_timestamp,
        save_snapshot_before=save_snapshot_before,
        save_snapshot_after=save_snapshot_after,
    )


class SortOrder(str, Enum):
    ASC  = "asc"
    DESC = "desc"


@main_cli.command()
def list_snapshots(
    ctx: typer.Context,
    limit: Annotated[int, typer.Option(help="The number of snapshots to list. Default is 10.")] = 10,
    offset: Annotated[int, typer.Option(help="The offset to start listing snapshots. Default is 0.")] = 0,
    order: Annotated[SortOrder, typer.Option(help="The order to list snapshots. Default is 'desc'.")] = "desc"
):
    snapshooter: Snapshooter = ctx.obj
    snapshot_paths = snapshooter.get_snapshot_paths()
    snapshot_paths = sorted(snapshot_paths, key=natural_sort_key, reverse=(order == "desc"))
    snapshot_paths = snapshot_paths[offset:offset+limit]
    for snapshot_path in snapshot_paths:
        print(snapshot_path)
    if offset + limit < len(snapshot_paths):
        print("...")


class DiffState(Enum):
    ONLY_LEFT = "only_left"
    ONLY_RIGHT = "only_right"
    DIFFERENT = "different"
    EQUAL = "equal"


@main_cli.command()
def compare_snapshots(
    ctx: typer.Context,
    path1      : Annotated[str, typer.Option(help="The path to the snapshot1 file to restore. If not set, then it will look for the latest snapshot available, that fulfills the --latest1 timestamp if provided")] = None,
    latest1    : Annotated[str, typer.Option(help="If set, then look for the latest snapshot1 before or at this timestamp. Expected format is 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS[offset]'.")] = None,
    path2      : Annotated[str, typer.Option(help="The path to the snapshot2 file to restore. If not set, then it will look for the latest snapshot available, that fulfills the --latest2 timestamp if provided")] = None,
    latest2    : Annotated[str, typer.Option(help="If set, then look for the latest snapshot2 before or at this timestamp. Expected format is 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS[offset]'.")] = None,
    diff_state : Annotated[List[DiffState], typer.Option(help="The statuses to keep. Default is 'different'.", )] = None,
):
    snapshooter: Snapshooter = ctx.obj
    snap1 = snapshooter.read_snapshot(snapshot_path=path1, latest_timestamp=latest1)
    snap2 = snapshooter.read_snapshot(snapshot_path=path2, latest_timestamp=latest2)
    df_snap1 = convert_snapshot_to_df(snap1)
    df_snap2 = convert_snapshot_to_df(snap2)
    df_diff = compare_snapshots_(df_snap1, df_snap2)
    if diff_state is not None:
        diff_state = [state.value for state in diff_state]
        df_diff = df_diff[df_diff["status"].isin(diff_state)]
    typer.echo(df_diff.to_markdown())


if __name__ == '__main__':
    main_cli()
