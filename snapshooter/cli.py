import json
import logging
from dataclasses import dataclass
from datetime import datetime
from enum import Enum
from typing import Tuple, List, Literal

import fsspec
import typer
from fsspec import AbstractFileSystem
from typing_extensions import Annotated

from snapshooter import Heap, Snapshooter, convert_snapshot_to_df, compare_snapshots as compare_snapshots_

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# get root logger
root_logger = logging.getLogger()
# shift logging for azure.core.pipeline.policies.http_logging_policy (if root logger is set to INFO, then set this to WARNING and so one)
logging.getLogger("azure.core.pipeline.policies.http_logging_policy").setLevel(root_logger.getEffectiveLevel() + 10)

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
):
    file_storage_options_dict = json.loads(file_storage_options or "{}")
    heap_storage_options_dict = json.loads(heap_storage_options or "{}")
    snap_storage_options_dict = json.loads(snap_storage_options or "{}")

    file_fs, file_root = fsspec.url_to_fs(file_root, **file_storage_options_dict)
    heap_fs, heap_root = fsspec.url_to_fs(heap_root, **heap_storage_options_dict)
    snap_fs, snap_root = fsspec.url_to_fs(snap_root, **snap_storage_options_dict)

    heap = Heap(heap_fs=heap_fs, heap_root=heap_root)
    snapshooter = Snapshooter(
        file_fs                 = file_fs, 
        file_root               = file_root, 
        snap_fs                 = snap_fs, 
        snap_root               = snap_root, 
        heap                    = heap,
        parallel_copy_to_heap   = parallel_copy_to_heap,
        parallel_copy_to_file   = parallel_copy_to_file,
        parallel_delete_in_file = parallel_delete_in_file,
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
    snapshooter.make_snapshot(
        save_snapshot=save_snapshot,
        download_missing_files=download_missing_files
    )


@main_cli.command()
def restore_snapshot(
    ctx                  : typer.Context,
    path                 : Annotated[str, typer.Argument(help="The path to the snapshot file to restore. If not set, then it will look for the latest snapshot available, that fulfills the --latest timestamp if provided")] = None,
    latest               : Annotated[str, typer.Argument(help="If set, then look for the latest snapshot before or at this timestamp. Expected format is 'YYYY-MM-DD' or 'YYYY-MM-DDTHH:MM:SS[offset]'.")] = None,
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


@main_cli.command()
def list_snapshots(
    ctx: typer.Context,
):
    snapshooter: Snapshooter = ctx.obj
    snapshot_paths = snapshooter.get_snapshot_paths()
    for snapshot_path in snapshot_paths:
        typer.echo(snapshot_path)


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
