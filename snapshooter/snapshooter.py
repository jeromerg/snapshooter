from abc import abstractmethod
import datetime
import gzip
import hashlib
import json
import logging
import os
import queue
import re
import threading
import time
import traceback
import uuid
from contextlib import contextmanager
from io import BufferedReader
from typing import Any, Generator, List, Dict, Set, Callable

import fsspec
import pandas as pd
from fsspec import AbstractFileSystem
import concurrent.futures
from .fsspec_utils import get_md5_getter, jsonify_file_info, natural_sort_key, patch_AbstractFileSystem_str_function
from .jsonl_utils import dumps_jsonl, loads_jsonl

log = logging.getLogger(__name__)


patch_AbstractFileSystem_str_function()


# noinspection RegExpRedundantEscape
FMT_PLACEHOLDER_REGEX  = re.compile(r"\{[^\}]*?\}")
DEFAULT_SRC_FS         = fsspec.filesystem("file")
DEFAULT_SNAP_ROOT      = os.path.normpath(os.path.abspath("./data/backup/snapshots"))
DEFAULT_SNAP_FS        = fsspec.filesystem("file")
SNAP_TIMESTAMP_UTC_FMT = "%Y-%m-%d_%H-%M-%S_%fZ"
SNAP_PATH_FMT          = f"{{timestamp_utc:%Y}}/{{timestamp_utc:%m}}/{{timestamp_utc:{SNAP_TIMESTAMP_UTC_FMT}}}.jsonl.gz"
DEFAULT_HEAP_ROOT      = os.path.normpath(os.path.abspath("./data/backup/heap"))
DEFAULT_HEAP_FS        = fsspec.filesystem("file")
HEAP_FMT_FN            = lambda md5: f"{md5[:2]}/{md5[2:4]}/{md5[4:6]}/{md5}.gz"
BLOCK_SIZE             = 8 * 1024 * 1024  # 8MB


def _coerce_fs(fs: AbstractFileSystem | str) -> AbstractFileSystem:
    if isinstance(fs, str):
        return fsspec.filesystem(fs)
    elif isinstance(fs, AbstractFileSystem):
        return fs
    else:
        raise Exception(f"Unknown type {type(fs)}. Accepted: str, AbstractFileSystem")


def _coerce_root_dir(fs: AbstractFileSystem, root: str) -> str:
    root = root.strip()
    # noinspection PyProtectedMember
    root = fs._strip_protocol(root)
    root = root.replace("\\", "/")
    root = root.rstrip("/")
    if root == "":
        root = "/"
    return root


def convert_snapshot_to_df(snapshot: List[dict]) -> pd.DataFrame:
    if not isinstance(snapshot, list):
        raise Exception(f"convert_snapshot_to_df: Unknown type {type(snapshot)} for snapshot. Expected: List[dict]")

    if len(snapshot) == 0:
        return pd.DataFrame(columns=["name", "md5"]).set_index("name")
    else:
        return pd.DataFrame(snapshot).set_index("name")


def compare_snapshots(
    left_snapshot: pd.DataFrame,
    right_snapshot: pd.DataFrame,
) -> pd.DataFrame:
    if not isinstance(left_snapshot, pd.DataFrame):
        raise Exception(f"compare_snapshots: Unknown type {type(left_snapshot)} for left_snapshot. Expected: pd.DataFrame")
    if not isinstance(right_snapshot, pd.DataFrame):
        raise Exception(f"compare_snapshots: Unknown type {type(right_snapshot)} for right_snapshot. Expected: pd.DataFrame")

    # merge left and right snapshot and compare md5s
    df = pd.merge(
        left_snapshot.add_suffix("_left"),
        right_snapshot.add_suffix("_right"),
        how="outer", left_index=True, right_index=True
    )
    df["status"] = "equal"
    df.loc[df["md5_left"] != df["md5_right"], "status"] = "different"
    df.loc[df["md5_left"].isna(), "status"] = "only_right"
    df.loc[df["md5_right"].isna(), "status"] = "only_left"

    # sort columns by name
    df = df.reindex(sorted(df.columns), axis=1)

    # sort by name
    df = df.sort_values(by=["name"])

    # print stats
    stats_json = df.groupby("status").size().to_dict()
    log.info(f"Snapshot comparison stats: {stats_json}")

    return df


class Heap:
    def __init__(
        self,
        heap_fs   : AbstractFileSystem,
        heap_root : str,
    ) -> None:
        """ Create a new Snapshooter instance.

        :param heap_fs: The file system of the heap files.
        :param heap_root: The root directory of the heap files.
        """
        self.heap_fs   = _coerce_fs(heap_fs)
        self.heap_root = _coerce_root_dir(heap_fs, heap_root)
        # Get all heap files
        log.info(f"List out heap files in {self.heap_root}")
        lister = ParallelLister(
            fs=self.heap_fs,
            root=self.heap_root,
            parallel_listers=10,
        )
        heap_file_paths = [fi["name"] for fi in lister.list_files()]
        # basename WITHOUT EXTENSION corresponds to the md5
        self.heap_md5s = set([os.path.basename(p).split(".")[0] for p in heap_file_paths])
        log.info(f"Heap initialized: Found {len(self.heap_md5s)} files in heap")

    def add_file_to_heap(self, f: BufferedReader, check_interrupted_fn: Callable) -> str:
        temp_file_path = f"{self.heap_root}/temp/{uuid.uuid4()}.gz"
        self.heap_fs.makedirs(f"{self.heap_root}/temp", exist_ok=True)
        md5_digester = hashlib.md5()
        try:
            with (
                self.heap_fs.open(temp_file_path, "wb") as temp_file,
                gzip.GzipFile(fileobj=temp_file, mode='wb') as temp_file
            ):
                while True:
                    check_interrupted_fn()
                    block = f.read(BLOCK_SIZE)
                    if not block:
                        break
                    temp_file.write(block)
                    md5_digester.update(block)

            md5 = md5_digester.hexdigest()

            # move temp file to heap
            heap_file_path_relative = HEAP_FMT_FN(md5)
            heap_file_path          = f"{self.heap_root}/{heap_file_path_relative}"

            if md5 in self.heap_md5s:
                log.debug(f"MD5 '{md5}' already exists in heap, skipping")
                return md5

            log.debug(f"Saving file with md5 '{md5}' to '{heap_file_path_relative}'")
            self.heap_fs.makedirs(os.path.dirname(heap_file_path), exist_ok=True)
            self.heap_fs.mv(temp_file_path, heap_file_path)
            self.heap_md5s.add(md5)
            return md5
        except:
            # if something went wrong, the temp file may still exist and should be removed
            try:
                self.heap_fs.rm(temp_file_path)
                log.debug(f"Removed temp file '{temp_file_path}'")
            except Exception as e:
                log.exception(f"Error removing temp file '{temp_file_path}'")
            raise

    @contextmanager
    def open(self, md5: str) -> Generator[BufferedReader, Any, Any]:
        heap_file_path_relative = HEAP_FMT_FN(md5)
        heap_file_path = f"{self.heap_root}/{heap_file_path_relative}"
        with self.heap_fs.open(heap_file_path, "rb") as heap_file, gzip.GzipFile(fileobj=heap_file, mode='rb') as heap_file:
            yield heap_file


class Snapshooter:
    def __init__(
        self,
        file_fs   : AbstractFileSystem,
        file_root : str,
        snap_fs   : AbstractFileSystem,
        snap_root : str,
        heap      : Heap,
        parallel_copy_to_heap   : int = 10,
        parallel_copy_to_file   : int = 10,
        parallel_delete_in_file : int = 10,
    ) -> None:
        """ Create a new Snapshooter instance.

        :param file_fs: The file system of the source files.
        :param file_root: The root directory of the source files.
        :param snap_fs: The file system of the snapshot files.
        :param snap_root: The root directory of the snapshot files.
        :param heap: The heap instance, that stores the files by their checksum.
        """
        self.file_fs   : AbstractFileSystem = _coerce_fs(file_fs)
        self.file_root : str                = _coerce_root_dir(file_fs, file_root)
        self.snap_fs   : AbstractFileSystem = _coerce_fs(snap_fs)
        self.snap_root : str                = _coerce_root_dir(snap_fs, snap_root)
        self.heap      : Heap               = heap
        self.parallel_copy_to_heap   : int  = parallel_copy_to_heap
        self.parallel_copy_to_file   : int  = parallel_copy_to_file
        self.parallel_delete_in_file : int  = parallel_delete_in_file
        

    def convert_snapshot_timestamp_to_path(self, timestamp: datetime.datetime) -> str:
        """ Convert the given timestamp to a snapshot file path.

        :param timestamp: The timestamp of the snapshot.
        :return: The path of the snapshot file.
        """
        # if timestamp is naiv, then assume local time
        if timestamp.tzinfo is None or timestamp.tzinfo.utcoffset(timestamp) is None:
            timestamp = timestamp.astimezone()
        
        # convert to utc
        timestamp_utc = timestamp.astimezone(datetime.timezone.utc)
        
        # create snapshot file path
        snap_file_path = SNAP_PATH_FMT.format(timestamp_utc=timestamp_utc)
        snap_file_path = f"{self.snap_root}/{snap_file_path}"

        return snap_file_path

    def convert_snapshot_path_to_timestamp(self, snap_file_path: str) -> datetime.datetime:
        """ Extract from the given snapshot file path the timestamp of the snapshot.

        :param snap_file_path: The path of the snapshot file.
        :return: The timestamp of the snapshot.
        """
        snap_file_name     = os.path.basename(snap_file_path)
        snap_timestamp_str = snap_file_name.split(".")[0]
        snap_timestamp     = datetime.datetime.strptime(snap_timestamp_str, SNAP_TIMESTAMP_UTC_FMT)
        snap_timestamp     = snap_timestamp.replace(tzinfo=datetime.timezone.utc)
        return snap_timestamp

    def get_snapshot_paths(self) -> list[str]:
        """ Get all snapshot paths from the snapshot file system.

        :return: The paths of all snapshots sorted by path name (descending).
        """
        snap_glob      = FMT_PLACEHOLDER_REGEX.sub("*", SNAP_PATH_FMT)
        snapshot_files = self.snap_fs.glob(f"{self.snap_root}/{snap_glob}")
        return snapshot_files

    def try_get_snapshot_path(
        self, 
        latest_timestamp: datetime.datetime | None = None
    ) -> str | None:
        """ Tries to get the latest snapshot path from the snapshot file system. If before is given, tries to get the latest snapshot path which was created before or at the given timestamp.

        :param latest_timestamp: If given, search for the latest snapshot which was created before or at the given timestamp. Default: None
        :return: The path of the latest snapshot or None, if no snapshot was found.
        """
        log.info("Search latest snapshot")
        snapshot_paths = self.get_snapshot_paths()
        if len(snapshot_paths) == 0:
            log.info(f"No snapshot found in {self.snap_root}")
            return None

        snapshot_path_by_filename = { f.split("/")[-1]: f for f in snapshot_paths }
        snapshot_filenames_in_reverse_order = sorted(snapshot_path_by_filename.keys(), key=natural_sort_key, reverse=True)

        # slice the list of snapshots to the one before the given timestamp    
        snapshot_path = None
        if latest_timestamp is not None:
            # if timestamp is naiv, then assume local time
            if latest_timestamp.tzinfo is None or latest_timestamp.tzinfo.utcoffset(latest_timestamp) is None:
                latest_timestamp = latest_timestamp.astimezone()
            # convert to utc
            latest_timestamp_utc = latest_timestamp.astimezone(datetime.timezone.utc)
            limit_snapshot_relative_path = SNAP_PATH_FMT.format(timestamp_utc=latest_timestamp_utc)
            limit_snapshot_filename = limit_snapshot_relative_path.split("/")[-1]
            for filename in snapshot_filenames_in_reverse_order:
                if filename <= limit_snapshot_filename:
                    snapshot_path = snapshot_path_by_filename[filename]
                    break
            if snapshot_path is None:
                log.info(f"No snapshot found in {self.snap_root} with timestamp before (or equal) '{latest_timestamp}'")
                return None
        else:
            snapshot_path = snapshot_path_by_filename[snapshot_filenames_in_reverse_order[0]]
        
        log.info(f"Found snapshot '{snapshot_path}'")
        return snapshot_path

    def try_read_snapshot(
        self, 
        snapshot_path: str | None = None,
        latest_timestamp: datetime.datetime | None = None,
    ) -> List[Dict] | None:
        if snapshot_path is None:
            if latest_timestamp is None:
                log.info(f"Try read latest snapshot")
            else:
                log.info(f"Try read latest snapshot before '{latest_timestamp}'")
            snapshot_path = self.try_get_snapshot_path(latest_timestamp)
            if snapshot_path is None:
                return None
        else:
            log.info(f"Try read snapshot from provided path '{snapshot_path}'")

        log.info(f"Read snapshot from {snapshot_path}")

        with self.snap_fs.open(snapshot_path, "rb") as f, gzip.GzipFile(fileobj=f) as g:
            text = g.read().decode("utf-8")
            latest_snapshot = loads_jsonl(text)  # type: List[Dict]

        log.info(f"Read snapshot contains {len(latest_snapshot)} files")
        return latest_snapshot

    def read_snapshot(
        self,
        snapshot_path: str | None = None,
        latest_timestamp: datetime.datetime | None = None,
    ) -> List[dict]:
        latest_snapshot = self.try_read_snapshot(snapshot_path=snapshot_path, latest_timestamp=latest_timestamp)
        if latest_snapshot is None:
            raise Exception(f"No snapshot found in '{self.snap_root}'")
        return latest_snapshot

    def _make_snapshot_without_md5(self) -> List[dict]:
        log.info(f"List out src files in '{self.file_root}' (may last long)")
        lister = ParallelLister(
            fs=self.file_fs,
            root=self.file_root,
            parallel_listers=10,
        )
        src_file_infos = lister.list_files()
        log.info(f"Found {len(src_file_infos)} src files")

        # convert native objects to json serializable objects
        src_file_infos = jsonify_file_info(src_file_infos)

        # remove the src_root from the file names    
        regex = re.compile(rf"^{re.escape(self.file_root)}/")
        for file_info in src_file_infos:
            file_info["name"] = regex.sub("", file_info["name"])

        # sort by name (relative path to root)
        src_file_infos.sort(key=lambda fi: fi["name"])
        
        return src_file_infos

    def _try_enrich_src_file_infos_with_md5_without_downloading(
        self,
        src_file_infos: List[dict],
        latest_snapshot: List[dict]
    ):
        """ This function uses a previous snapshot and tries to find in it the same file name, then verifies if
        the file is the same by using file system specific way to identify same file (e.g. ETAG) and if it is the same
        it copies the md5 from the previous snapshot to the current file info."""
        # get md5 getter function depending on the file system type            
        md5_getter = get_md5_getter(self.file_fs)

        # convert list to dict for faster lookup
        latest_snapshot_file_info_by_file_name = {file_info["name"]: file_info for file_info in latest_snapshot}

        # try to get md5 from file info and latest snapshot
        for src_file_info in src_file_infos:
            md5 = md5_getter(src_file_info, latest_snapshot_file_info_by_file_name)
            if md5 is not None:
                src_file_info["md5"] = md5

    def make_snapshot(
        self,
        save_snapshot: bool = True,
        download_missing_files: bool = True
    ) -> tuple[List[dict], datetime.datetime]:
        timestamp = datetime.datetime.now(datetime.timezone.utc)
        log.info(f"Making Snapshot with timestamp = '{timestamp}'")

        log.info(f"Retrieving prior snapshot to optimize download...")
        latest_snapshot = self.try_read_snapshot(latest_timestamp=timestamp)
        if latest_snapshot is None:
            latest_snapshot = []
        log.info(f"Prior snapshot retrieved")

        snapshot = self._make_snapshot_without_md5()
        self._try_enrich_src_file_infos_with_md5_without_downloading(snapshot, latest_snapshot)

        snapshot_files_by_name = {file_info["name"]: file_info for file_info in snapshot}

        file_names_without_md5 = {fi["name"] for fi in snapshot if "md5" not in fi or fi["md5"] is None}
        if len(file_names_without_md5) > 0:
            log.info(f"Found {len(file_names_without_md5)} files with missing md5... downloads required")

        if download_missing_files:
            file_names_missings = {fi["name"] for fi in snapshot if "md5" not in fi or fi["md5"] not in self.heap.heap_md5s}
            if len(file_names_missings) > 0:
                log.info(f"Found {len(file_names_missings)} missing files not in heap... downloads required")
        else:
            file_names_missings = set()

        all_file_names_to_download = file_names_without_md5 | file_names_missings
        if len(all_file_names_to_download) > 0:
            log.info(f"Downloading {len(all_file_names_to_download)} files to heap")

            downloader = ParallelCopyFileToHeap(
                file_fs                = self.file_fs,
                file_root              = self.file_root,
                relative_paths_to_copy = all_file_names_to_download,
                heap                   = self.heap,
                parallelization        = self.parallel_copy_to_heap,
                snapshot_files_by_name = snapshot_files_by_name,
            )
            downloader.process_files()

        if save_snapshot:
            self._save_snapshot(snapshot, timestamp)

        return snapshot, timestamp

    def _save_snapshot(
        self,
        snapshot: List[dict],
        snapshot_timestamp: datetime
    ) -> str:
        """Save the given snapshot to the snapshot file system.

        :param snapshot: The snapshot to save.
        :param snapshot_timestamp: The timestamp of the snapshot to save.
        :return: The (absolute) path of the saved snapshot.
        """
        new_snapshot_relative_path = SNAP_PATH_FMT.format(timestamp_utc=snapshot_timestamp)
        new_snapshot_path = f"{self.snap_root}/{new_snapshot_relative_path}"
        log.info(f"Save snapshot to {new_snapshot_path}")
        self.snap_fs.makedirs(os.path.dirname(new_snapshot_path), exist_ok=True)
        with self.snap_fs.open(new_snapshot_path, "wb") as f, gzip.GzipFile(fileobj=f, mode='wb') as g:
            snap_content = dumps_jsonl(snapshot)
            g.write(snap_content.encode("utf-8"))
        log.info(f"Saved snapshot")
        return new_snapshot_path

    def restore_snapshot(
        self,
        snapshot_to_restore: str | List[dict] | pd.DataFrame | None = None,
        latest_timestamp: datetime.datetime = None,
        save_snapshot_before: bool = True,
        save_snapshot_after: bool = False,
    ):
        log.info("Loading snapshot to restore")
        # read snapshot depending on the type of snapshot_to_restore
        if snapshot_to_restore is None:
            snap = self.read_snapshot(latest_timestamp=latest_timestamp)
            df_snapshot_to_restore = convert_snapshot_to_df(snap)
        elif isinstance(snapshot_to_restore, str):
            snap = self.read_snapshot(snapshot_path=snapshot_to_restore)
            df_snapshot_to_restore = convert_snapshot_to_df(snap)
        elif isinstance(snapshot_to_restore, list):
            df_snapshot_to_restore = convert_snapshot_to_df(snapshot_to_restore)
        elif isinstance(snapshot_to_restore, pd.DataFrame):
            df_snapshot_to_restore = snapshot_to_restore
        else:
            raise Exception(f"restore_snapshot: Unknown type {type(snapshot_to_restore)} for snapshot_to_restore. Expected: pd.DataFrame, List[dict]")
        log.info(f"Snapshot to restore loaded")

        log.info("Making current snapshot to apply diff to")
        current_snapshot, _ = self.make_snapshot(save_snapshot=save_snapshot_before, download_missing_files=True)
        log.info(f"Current snapshot made")
        df_current_snapshot = convert_snapshot_to_df(current_snapshot)

        df_diff = compare_snapshots(df_snapshot_to_restore, df_current_snapshot)

        self.apply_diff(df_diff)
        
        # finally save the latest state if requested
        # Remark: There is space for optimization, because the snapshot 
        # could be built from the snapshot before and the diff + missing file metadata for
        # the files that were not in the snapshot before.
        if save_snapshot_after:
            self.make_snapshot(save_snapshot=True, download_missing_files=True)

    def apply_diff(self, df_diff: pd.DataFrame):
        if not isinstance(df_diff, pd.DataFrame):
            raise Exception(f"apply_diff: Unknown type {type(df_diff)} for diff. Expected: pd.DataFrame")

        relative_path_only_left  = set(df_diff[df_diff["status"] == "only_left"].index)
        relative_path_only_right = set(df_diff[df_diff["status"] == "only_right"].index)
        relative_path_different  = set(df_diff[df_diff["status"] == "different"].index)

        log.info(f"Copying files: {len(relative_path_only_left)} only_left + {len(relative_path_different)} different")
        relative_paths_to_copy = sorted(relative_path_only_left | relative_path_different)
        parallel_job = ParallelCopyHeapToFile(
            file_fs                = self.file_fs,
            file_root              = self.file_root,
            relative_paths_to_copy = relative_paths_to_copy,
            heap                   = self.heap,
            parallelization        = self.parallel_copy_to_file,            
            df_diff                = df_diff,
        )
        parallel_job.process_files()

        log.info(f"Deleting {len(relative_path_only_right)} files")
        parallel_job = ParallelDeleteFile(
            file_fs                = self.file_fs,
            file_root              = self.file_root,
            relative_paths_to_copy = relative_path_only_right,
            heap                   = self.heap,
            parallelization        = self.parallel_delete_in_file,
        )
        parallel_job.process_files()


class ParallelLister:
    def __init__(
        self,
        fs: AbstractFileSystem,
        root: str,
        parallel_listers: int,
    ):
        self.fs = fs
        self.root = root
        self.parallel_listers = parallel_listers
        self.dir_queue = queue.Queue()
        self.result = []
        self.errors = []
        self.lock = threading.Lock()
        self._is_interrupted = False

    def check_interrupted(self):
        if self._is_interrupted:
            raise InterruptedError("Interrupted properly")

    def interrupt(self):
        old_is_interrupted = self._is_interrupted
        self._is_interrupted = True
        # Wake up all threads waiting on the queue by putting None as a special value
        if not old_is_interrupted:
            for _ in range(self.parallel_listers):
                self.dir_queue.put(None)

    def _list_dir(self, directory: str):
            # all_details  = self.fs.find(directory, detail=True, withdirs=True, maxdepth=0).values()
            # all_details  = list(all_details)
            all_details    = self.fs.ls(directory, detail=True)
            all_details    = [d for d in all_details if d["name"] != directory]  # avoid endless loop
            sub_dir_infos  = [d for d in all_details if d['type'] == 'directory']
            src_file_infos = [d for d in all_details if d['type'] == 'file']
            for sub_dir_info in sub_dir_infos:
                self.dir_queue.put(sub_dir_info['name'])
            with self.lock:
                self.result.extend(src_file_infos)

    def _process_queue(self):
        try:
            while not self._is_interrupted:
                self.check_interrupted()
                directory = self.dir_queue.get()
                self.check_interrupted()
                try:
                    self._list_dir(directory)
                except Exception as e:
                    error_message = f"Error listing files in {directory}: {e}"
                    error_message += "\n" + traceback.format_exc()
                    with self.lock:
                        self.errors.append(error_message)
                self.dir_queue.task_done()
        except InterruptedError:
            pass  # some tools log error is thread die because of exception, but we want to ignore this excepted case

    def _log_progress(self):
        try:
            while True:
                time.sleep(10)
                self.check_interrupted()
                log.info(f"Progress: {len(self.result)} files listed.")
        except InterruptedError:
            # some tools log error is thread die because of exception, but we want to ignore this excepted case
            log.debug("ParallelLister: Logging thread interrupted properly.")

    def list_files(self):
        # remark: first connection and root check in main thread improves error handling
        try:
            root_exists = self.fs.exists(self.root)
        except Exception as e:
            raise Exception(f"Error verifying root folder '{self.root}' in {self.fs}: {e}") from e

        if not root_exists:
            raise Exception(f"Root folder '{self.root}' does not exist in {self.fs}")
        else:
            log.info(f"Root folder '{self.root}' exists in {self.fs}. Now listing files...")
            
        # even first root dir search in main threa ensures that access rights are ok
        try:
            self._list_dir(self.root)
        except Exception as e:
            raise Exception(f"Error listing files of root folder '{self.root}' in {self.fs}: {e}") from e
            
        # start parallelized listing
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.parallel_listers) as executor:
            for _ in range(self.parallel_listers):
                executor.submit(self._process_queue)

            log_thread = threading.Thread(target=self._log_progress, daemon=True)
            log_thread.start()

            # Add root and wait for the directory listing tasks to complete
            self.dir_queue.join()

            # for logger
            self.interrupt()

        # Check if there were any errors during listing
        if self.errors:
            error_summary = "\n---------------------------\n".join(self.errors)
            raise Exception(f"Errors occurred during file listing:\n{error_summary}")

        return self.result


class ParallelFileProcessor:
    """Abstract class for parallel file processing."""
    def __init__(
        self,
        file_fs                : AbstractFileSystem,
        file_root              : str,
        relative_paths_to_copy : Set[str],
        heap                   : Heap,
        parallelization        : int,
    ):
        self.file_fs                   = file_fs
        self.file_root                 = file_root
        self.relative_paths_to_process = relative_paths_to_copy
        self.heap                      = heap
        self.parallelization           = parallelization
        self.copy_count                = 0
        self.errors                    = []  # List to store errors
        self.lock                      = threading.Lock()
        self._is_interrupted           = False

    def check_interrupted(self):
        if self._is_interrupted:
            raise InterruptedError(f"{self.__class__.__name__}: Interrupted properly")

    def interrupt(self):
        self._is_interrupted = True

    def process_files(self):
        log.info(f"{self.__class__.__name__}: Processing {len(self.relative_paths_to_process)} files with {self.parallelization} workers")
        with concurrent.futures.ThreadPoolExecutor(max_workers=self.parallelization) as executor:
            log_thread = threading.Thread(target=self._log_progress, daemon=True)
            log_thread.start()

            # Submit all download tasks to the executor
            futures = [executor.submit(self._process_file, file_relative_path) for file_relative_path in self.relative_paths_to_process]

            # Wait for all futures to complete
            try:
                concurrent.futures.wait(futures)
            except KeyboardInterrupt:
                self.interrupt()
                log.info(f"{self.__class__.__name__}: Download interrupted by user.")
                executor.shutdown(wait=False)
                raise

        # Ensures the logging thread also completes
        self.interrupt()

        # Check if there were any errors during downloads
        if self.errors:
            error_summary = "\n---------------------------\n".join(self.errors)
            raise Exception(f"{self.__class__.__name__}: Errors occurred during files processing:\n{error_summary}")

    @abstractmethod
    def _process_file(self, file_relative_path):
        ...

    def _log_progress(self):
        try:
            while True:
                time.sleep(10)
                self.check_interrupted()
                log.info(f"{self.__class__.__name__}: Progress ... {self.copy_count}/{len(self.relative_paths_to_process)} files processed.")
        except InterruptedError:
            # some tools log error is thread die because of exception, but we want to ignore this excepted case
            log.debug(f"{self.__class__.__name__}: Logging thread interrupted properly.")


class ParallelCopyFileToHeap(ParallelFileProcessor):
    def __init__(
        self,
        file_fs                : AbstractFileSystem,
        file_root              : str,
        relative_paths_to_copy : Set[str],
        heap                   : Heap,
        parallelization        : int,
        snapshot_files_by_name : Dict[str, dict],
    ):
        super().__init__(file_fs, file_root, relative_paths_to_copy, heap, parallelization)        
        self.snapshot_files_by_name = snapshot_files_by_name

    def _process_file(self, file_relative_path):
        try:
            src_file_info = self.snapshot_files_by_name[file_relative_path]
            src_file_path = f"{self.file_root}/{file_relative_path}"
            log.debug(f"{self.__class__.__name__}: Downloading '{file_relative_path}'")
            self.check_interrupted()
            with self.file_fs.open(src_file_path, "rb") as f:
                src_file_md5 = self.heap.add_file_to_heap(f, self.check_interrupted)

            if "md5" in src_file_info:
                if src_file_info["md5"] != src_file_md5:
                    error_message = f"MD5 mismatch for '{file_relative_path}' between snapshot metadata and downloaded file"
                    log.error(error_message)
                    with self.lock:
                        self.errors.append(error_message)
            else:
                src_file_info["md5"] = src_file_md5

        except Exception as e:
            error_message = f"Error copying {file_relative_path}: {e}"
            error_message += "\n" + traceback.format_exc()
            log.error(error_message)
            with self.lock:
                self.errors.append(error_message)
        finally:
            with self.lock:
                self.copy_count += 1


class ParallelCopyHeapToFile(ParallelFileProcessor):
    def __init__(
        self,
        file_fs                : AbstractFileSystem,
        file_root              : str,
        relative_paths_to_copy : Set[str],
        heap                   : Heap,
        parallelization        : int,
        df_diff                : pd.DataFrame,
    ):
        super().__init__(file_fs, file_root, relative_paths_to_copy, heap, parallelization)
        self.df_diff = df_diff

    def _process_file(self, file_relative_path):
        try:
            md5 = self.df_diff.at[file_relative_path, "md5_left"]
            src_file_path = f"{self.file_root}/{file_relative_path}"
            log.debug(f"Copying file with md5 '{md5}' to '{file_relative_path}'")
            self.file_fs.makedirs(os.path.dirname(src_file_path), exist_ok=True)  # TODO: Optimze to avoid calling every times
            with self.heap.open(md5) as src_file:
                with self.file_fs.open(src_file_path, "wb") as dst_file:
                    dst_file.write(src_file.read())
        except Exception as e:
            error_message = f"Error copying {file_relative_path}: {e}"
            error_message += "\n" + traceback.format_exc()
            log.error(error_message)
            with self.lock:
                self.errors.append(error_message)
        finally:
            with self.lock:
                self.copy_count += 1


class ParallelDeleteFile(ParallelFileProcessor):
    def __init__(
        self,
        file_fs                : AbstractFileSystem,
        file_root              : str,
        relative_paths_to_copy : Set[str],
        heap                   : Heap,
        parallelization        : int,
    ):
        super().__init__(file_fs, file_root, relative_paths_to_copy, heap, parallelization)

    def _process_file(self, file_relative_path):
        src_file_path = f"{self.file_root}/{file_relative_path}"
        log.debug(f"Deleting '{file_relative_path}'")
        self.file_fs.rm(src_file_path)
