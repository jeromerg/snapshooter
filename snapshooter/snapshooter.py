import datetime
import gzip
import hashlib
import json
import logging
import os
import re
from typing import List, Literal, overload
import fsspec
from fsspec import AbstractFileSystem
import pandas as pd

from .fsspec_utils import get_md5_getter, jsonify_file_info, natural_sort_key
from .jsonl_utils import dumps_jsonl, loads_jsonl

log = logging.getLogger(__name__)


FMT_PLACEHOLDER_REGEX = re.compile(r"\{[^\}]*?\}")
DEFAULT_SRC_FS        = fsspec.filesystem("file")
DEFAULT_SNAP_ROOT     = os.path.normpath(os.path.abspath("./data/backup/snapshots"))
DEFAULT_SNAP_FS       = fsspec.filesystem("file")
SNAP_TIMESTAMP_FMT    = "%Y-%m-%d_%H-%M-%S_%fZ"
SNAP_PATH_FMT         = f"{{timestamp:%Y}}/{{timestamp:%m}}/{{timestamp:{SNAP_TIMESTAMP_FMT}}}.jsonl.gz"
DEFAULT_HEAP_ROOT     = os.path.normpath(os.path.abspath("./data/backup/heap"))
DEFAULT_HEAP_FS       = fsspec.filesystem("file")
HEAP_FMT_FN           = lambda md5: f"{md5[:2]}/{md5[2:4]}/{md5[4:6]}/{md5}.gz"


def _coerce_fs(fs: AbstractFileSystem|str) -> AbstractFileSystem:
    if isinstance(fs, str):
        return fsspec.filesystem(fs)
    elif isinstance(fs, AbstractFileSystem):
        return fs
    else:
        raise Exception(f"Unknown type {type(fs)}. Accepted: str, AbstractFileSystem")


def _coerce_root_dir(fs: AbstractFileSystem, root: str) -> str:
    root = root.strip()
    root = fs._strip_protocol(root)
    root = root.replace("\\", "/")
    root = root.rstrip("/")
    if root == "":
        root = "/"
    return root


def _convert_snapshot_as_required(snapshot: List[dict], as_df: bool) -> List[dict]|pd.DataFrame:
    if as_df:
        if isinstance(snapshot, pd.DataFrame):
            if snapshot.index.name == "name":
                return snapshot
            if "name" not in snapshot.columns:
                raise Exception(f"DataFrame must have a 'name' column or the row index named 'name'")
            snapshot = snapshot.set_index("name")
            return snapshot
        elif isinstance(snapshot, list):
            if len(snapshot) == 0:
                return pd.DataFrame(columns=["name", "md5"]).set_index("name")
            else:
                return pd.DataFrame(snapshot).set_index("name")
        else:
            raise Exception(f"Unknown type {type(snapshot)}. Accepted: List[dict], pd.DataFrame")
    else:
        if isinstance(snapshot, pd.DataFrame):
            return snapshot.reset_index().to_dict("records")
        elif isinstance(snapshot, list):
            return snapshot
        else:
            raise Exception(f"Unknown type {type(snapshot)}. Accepted: List[dict], pd.DataFrame")


class Snapshooter:
    def __init__(
        self,
        src_fs    : AbstractFileSystem,
        src_root  : str,
        snap_fs   : AbstractFileSystem,
        snap_root : str,
        heap_fs   : AbstractFileSystem,
        heap_root : str,
    ) -> None:
        """ Create a new Snapshooter instance.

        :param src_fs: The file system of the source files.
        :param src_root: The root directory of the source files.
        :param snap_fs: The file system of the snapshot files.
        :param snap_root: The root directory of the snapshot files.
        :param heap_fs: The file system of the heap files.
        :param heap_root: The root directory of the heap files.
        """
        self.src_fs    = _coerce_fs(src_fs)
        self.src_root  = _coerce_root_dir(src_fs, src_root)
        self.snap_fs   = _coerce_fs(snap_fs)
        self.snap_root = _coerce_root_dir(snap_fs, snap_root)
        self.heap_fs   = _coerce_fs(heap_fs)
        self.heap_root = _coerce_root_dir(heap_fs, heap_root)

    def convert_snapshot_timestamp_to_path(self, timestamp: datetime.datetime) -> str:
        """ Convert the given timestamp to a snapshot file path.

        :param timestamp: The timestamp of the snapshot.
        :return: The path of the snapshot file.
        """
        snap_file_path = SNAP_PATH_FMT.format(timestamp=timestamp)
        snap_file_path = f"{self.snap_root}/{snap_file_path}"
        return snap_file_path

    def convert_snapshot_path_to_timestamp(self, snap_file_path: str) -> datetime.datetime:
        """ Extract from the given snapshot file path the timestamp of the snapshot.

        :param snap_file_path: The path of the snapshot file.
        :return: The timestamp of the snapshot.
        """
        snap_file_name     = os.path.basename(snap_file_path)
        snap_timestamp_str = snap_file_name.split(".")[0]
        snap_timestamp     = datetime.datetime.strptime(snap_timestamp_str, SNAP_TIMESTAMP_FMT)
        return snap_timestamp

    def get_snapshot_paths(self) -> list[str]:
        """ Get all snapshot paths from the snapshot file system.

        :return: The paths of all snapshots sorted by path name (descending).
        """
        snap_glob      = FMT_PLACEHOLDER_REGEX.sub("*", SNAP_PATH_FMT)
        snapshot_files = self.snap_fs.glob(f"{self.snap_root}/{snap_glob}")
        snapshot_files = sorted(snapshot_files, key=natural_sort_key, reverse=True)
        return snapshot_files

    def try_get_snapshot_path(
        self, 
        before: datetime.datetime|None = None
    ) -> str|None:
        """ Tries to get the latest snapshot path from the snapshot file system. If before is given, tries to get the latest snapshot path which was created before or at the given timestamp.

        :param before: If given, search for the latest snapshot which was created before or at the given timestamp. Default: None
        :return: The path of the latest snapshot or None, if no snapshot was found.
        """
        log.info("Search latest snapshot")
        snapshot_files = self.get_snapshot_paths()
            
        if len(snapshot_files) == 0:
            log.info(f"No snapshot found in {self.snap_fs} / {self.snap_root}")
            return None

        # slice the list of snapshots to the one before the given timestamp    
        snapshot_path = None
        if before is not None:
            limit_snapshot_file = SNAP_PATH_FMT.format(timestamp=before)
            for f in snapshot_files:
                if f <= limit_snapshot_file:
                    snapshot_path = f
                    break
            if snapshot_path is None:
                log.info(f"No snapshot found in {self.snap_fs} / {self.snap_root} with timestamp before (or equal) '{before}'")
                return None
        else:
            snapshot_path = snapshot_files[0]
        
        log.info(f"Found snapshot '{snapshot_path}'")
        return snapshot_path

    @overload  # pragma: no cover
    def try_read_snapshot(self, as_df: Literal[True]) -> pd.DataFrame|None:
        """ Tries to read the latest snapshot from the snapshot file system.

        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :return: The latest snapshot as pandas DataFrame or None, if no snapshot was found.
        """
        ...
    @overload  # pragma: no cover
    def try_read_snapshot(self, as_df: Literal[False] = False) -> List[dict]|None:
        """ Tries to read the latest snapshot from the snapshot file system.

        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :return: The latest snapshot as pandas DataFrame or None, if no snapshot was found.
        """
        ...
    @overload  # pragma: no cover
    def try_read_snapshot(self, before: datetime.datetime, as_df: Literal[True]) -> pd.DataFrame|None:
        """ Tries to read the latest snapshot from the snapshot file system which was created before or at the given timestamp.

        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :return: The latest snapshot as pandas DataFrame or None, if no snapshot was found.
        """
        ...
    @overload  # pragma: no cover
    def try_read_snapshot(self, before: datetime.datetime, as_df: Literal[False] = False) -> List[dict]|None:
        """ Tries to read the latest snapshot from the snapshot file system which was created before or at the given timestamp.

        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :return: The latest snapshot as pandas DataFrame or None, if no snapshot was found.
        """
        ...

    @overload  # pragma: no cover
    def try_read_snapshot(self, snapshot_path: str, as_df: Literal[True]) -> pd.DataFrame|None:
        """ Tries to read the snapshot from the given path.

        :param snapshot_path: The path of the snapshot to read.
        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :return: The snapshot as pandas DataFrame or None, if no snapshot was found.
        """
        ...
    @overload  # pragma: no cover
    def try_read_snapshot(self, snapshot_path: str, as_df: Literal[False] = False) -> List[dict]|None:
        """ Tries to read the snapshot from the given path.

        :param snapshot_path: The path of the snapshot to read.
        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :return: The snapshot as pandas DataFrame or None, if no snapshot was found.
        """
        ...

    def try_read_snapshot(
        self, 
        snapshot_path: str|None = None, 
        before: datetime.datetime|None = None,
        as_df = False
    ) -> dict|pd.DataFrame|None:
        if snapshot_path is None:
            snapshot_path = self.try_get_snapshot_path(before)
            if snapshot_path is None:
                return None
            
        with self.snap_fs.open(snapshot_path, "rb") as f, gzip.GzipFile(fileobj=f) as g:
            latest_snapshot = loads_jsonl(g.read().decode("utf-8"))
        log.info(f"Loaded {len(latest_snapshot)} files from snapshot")

        return _convert_snapshot_as_required(latest_snapshot, as_df)

    @overload  # pragma: no cover
    def read_snapshot(self, as_df: Literal[True]) -> pd.DataFrame:
        """ Read the latest snapshot from the snapshot file system.

        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :return: The latest snapshot as pandas DataFrame.
        """
        ...
    @overload  # pragma: no cover
    def read_snapshot(self, as_df: Literal[False] = False) -> List[dict]:
        """ Read the latest snapshot from the snapshot file system.

        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :raises Exception: If no snapshot was found.
        :return: The latest snapshot as pandas DataFrame.
        """
        ...
    @overload  # pragma: no cover
    def read_snapshot(self, before: datetime.datetime, as_df: Literal[True]) -> pd.DataFrame:
        """ Read the latest snapshot from the snapshot file system which was created before or at the given timestamp.

        :param before: The timestamp of the snapshot to read.
        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :raises Exception: If no snapshot was found.
        :return: The latest snapshot as pandas DataFrame.
        """
        ...
    @overload  # pragma: no cover
    def read_snapshot(self, before: datetime.datetime, as_df: Literal[False] = False) -> List[dict]:
        """ Read the latest snapshot from the snapshot file system which was created before or at the given timestamp.

        :param before: The timestamp of the snapshot to read.
        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :raises Exception: If no snapshot was found.
        :return: The latest snapshot as pandas DataFrame.
        """
        ...
    @overload  # pragma: no cover
    def read_snapshot(self, snapshot_path: str, as_df: Literal[True]) -> pd.DataFrame:
        """ Read the snapshot from the given path.

        :param snapshot_path: The path of the snapshot to read.
        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :raises Exception: If no snapshot was found.
        :return: The snapshot as pandas DataFrame.
        """
        ...
    @overload  # pragma: no cover
    def read_snapshot(self, snapshot_path: str, as_df: Literal[False] = False) -> List[dict]:
        """ Read the snapshot from the given path.

        :param snapshot_path: The path of the snapshot to read.
        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :raises Exception: If no snapshot was found.
        :return: The snapshot as pandas DataFrame.
        """
        ...

    def read_snapshot(self, snapshot_path: str|None = None, before: datetime.datetime|None = None, as_df = False) -> List[dict]|pd.DataFrame:
        latest_snapshot = self.try_read_snapshot(snapshot_path, before, as_df)
        if latest_snapshot is None:
            raise Exception(f"No snapshot found in {self.snap_fs} / {self.snap_root}")        
        return latest_snapshot

    def _generate_snapshot_without_md5(self) -> List[dict]:
        log.info(f"List out src files in {self.src_fs} / {self.src_root}")
        src_file_infos: list = list(self.src_fs.find(self.src_root, withdirs=False, detail=True).values())
        log.info(f"Found {len(src_file_infos)} src files")

        # convert native objects to json serializable objects
        src_file_infos = jsonify_file_info(src_file_infos)

        # remove the src_root from the file names    
        regex = re.compile(rf"^{re.escape(self.src_root)}/")
        for file_info in src_file_infos:
            file_info["name"] = regex.sub("", file_info["name"])

        # sort by name (relative path to root)
        src_file_infos.sort(key=lambda file_info: file_info["name"])
        
        return src_file_infos

    def _try_enrich_src_file_infos_with_md5_without_downloading(
        self,
        src_file_infos: List[dict],
        latest_snapshot: List[dict]
    ):
        # get md5 getter function depending on the file system type            
        md5_getter = get_md5_getter(self.src_fs)

        # convert list to dict for faster lookup
        latest_snapshot_by_filename = {file_info["name"]: file_info for file_info in latest_snapshot}

        # try to get md5 from file info and latest snapshot
        for src_file_info in src_file_infos:
            md5 = md5_getter(src_file_info, latest_snapshot_by_filename)
            if md5 is not None:
                src_file_info["md5"] = md5

    @overload  # pragma: no cover
    def generate_snapshot(self, as_df:Literal[False]=False) -> tuple[List[dict], datetime.datetime]:
        """Generate a snapshot of the src file system.

        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :return: The snapshot as pandas DataFrame or list of dicts and the timestamp of the snapshot.
        """
        ...

    @overload  # pragma: no cover
    def generate_snapshot(self, as_df=Literal[True]) -> tuple[pd.DataFrame, datetime.datetime]:
        """Generate a snapshot of the src file system.

        :param as_df: If False (default), return the snapshot as list of dicts. If True, return the snapshot as pandas DataFrame.
        :return: The snapshot as pandas DataFrame or list of dicts and the timestamp of the snapshot.
        """
        ...
        
    def generate_snapshot(self, as_df=False) -> tuple[List[dict]|pd.DataFrame, datetime.datetime]:
        before = datetime.datetime.utcnow()
        log.info(f"Create Snapshot with timestamp = '{before}'")

        latest_snapshot = self.try_read_snapshot(before=before)
        if latest_snapshot is None:
            latest_snapshot = []

        snapshot = self._generate_snapshot_without_md5()
        self._try_enrich_src_file_infos_with_md5_without_downloading(snapshot, latest_snapshot)

        remaining_file_infos_without_md5 = [file_info for file_info in snapshot if "md5" not in file_info or file_info["md5"] is None]
        
        if len(remaining_file_infos_without_md5) > 0:
            log.info(f"Downloading {len(remaining_file_infos_without_md5)} files with missing md5")
        else:
            log.info(f"No missing md5s found in src file infos")
            
        for src_file_info in remaining_file_infos_without_md5:
            src_file_relative_path = src_file_info["name"]
            src_file_path = f"{self.src_root}/{src_file_relative_path}"
            log.debug(f"Downloading '{src_file_relative_path}'")
            with self.src_fs.open(src_file_path, "rb") as f:
                content_bytes = f.read()
                # calculate md5
                src_file_md5 = hashlib.md5(content_bytes).hexdigest()
                # store the value in the dict
                src_file_info["md5"] = src_file_md5
                # save the file to dst heap, if it does not exist
                heap_file_path_relative = HEAP_FMT_FN(src_file_md5)
                heap_file_path = f"{self.heap_root}/{heap_file_path_relative}"
                if self.heap_fs.exists(heap_file_path):
                    log.debug(f"MD5 '{src_file_md5}' already exists in heap, skipping")
                    continue
                log.debug(f"Saving '{src_file_relative_path}' to '{heap_file_path_relative}'")
                self.heap_fs.makedirs(os.path.dirname(heap_file_path), exist_ok=True)
                with self.heap_fs.open(heap_file_path, "wb") as heap_file:
                    heap_file.write(content_bytes)

        return _convert_snapshot_as_required(snapshot, as_df), before

    def save_snapshot(
        self,
        snapshot: List[dict]|pd.DataFrame,
        snapshot_timestamp: datetime
    ) -> str:
        """Save the given snapshot to the snapshot file system.

        :param snapshot: The snapshot to save.
        :param snapshot_timestamp: The timestamp of the snapshot to save.
        :return: The (absolute) path of the saved snapshot.
        """
        snapshot = _convert_snapshot_as_required(snapshot, as_df=False)

        new_snapshot_relative_path = SNAP_PATH_FMT.format(timestamp=snapshot_timestamp)
        new_snapshot_path = f"{self.snap_root}/{new_snapshot_relative_path}"
        log.info(f"Save snapshot to {self.snap_fs} / {new_snapshot_path}")
        self.snap_fs.makedirs(os.path.dirname(new_snapshot_path), exist_ok=True)
        with self.snap_fs.open(new_snapshot_path, "wb") as f, gzip.GzipFile(fileobj=f, mode='wb') as g:
            snap_content = dumps_jsonl(snapshot)
            g.write(snap_content.encode("utf-8"))
        log.info(f"Saved snapshot")
        return new_snapshot_path

    @overload  # pragma: no cover
    def compare_snapshots(
        self,
        left_snapshot: List[dict]|pd.DataFrame,
        right_snapshot: List[dict]|pd.DataFrame,
        as_df : Literal[True]
    ) -> pd.DataFrame:
        """Compare two snapshots and return the diff.

        :param left_snapshot: Left snapshot of the diff to compare.
        :param right_snapshot: Right snapshot of the diff to compare.
        :param as_df: If False (default), return the diff as list of dicts. If True, return the diff as pandas DataFrame.
        :return: The diff as pandas DataFrame or list of dicts.
        """
        ...
        
    
    @overload  # pragma: no cover
    def compare_snapshots(
        self,
        left_snapshot: List[dict]|pd.DataFrame,
        right_snapshot: List[dict]|pd.DataFrame,
        as_df : Literal[False] = False
    ) -> List[dict]:
        """Compare two snapshots and return the diff.

        :param left_snapshot: Left snapshot of the diff to compare.
        :param right_snapshot: Right snapshot of the diff to compare.
        :param as_df: If False (default), return the diff as list of dicts. If True, return the diff as pandas DataFrame.
        :return: The diff as pandas DataFrame or list of dicts.
        """
        ...
        
    
    def compare_snapshots(
        self,
        left_snapshot: List[dict]|pd.DataFrame,
        right_snapshot: List[dict]|pd.DataFrame,
        as_df = False
    ) -> pd.DataFrame|List[dict]:
        left_snapshot  = _convert_snapshot_as_required(left_snapshot, True)
        right_snapshot = _convert_snapshot_as_required(right_snapshot, True)
        
        # merge left and right snapshot and compare md5s
        df = pd.merge(
            left_snapshot.add_suffix("_left"), 
            right_snapshot.add_suffix("_right"), 
            how="outer", left_index=True, right_index=True
        )
        df["status"] = "equal"
        df.loc[df["md5_left" ] != df["md5_right"], "status"] = "different"
        df.loc[df["md5_left" ].isna(), "status"] = "only_right"
        df.loc[df["md5_right"].isna(), "status"] = "only_left"

        # sort columns by name
        df = df.reindex(sorted(df.columns), axis=1)

        # sort by name
        df = df.sort_values(by=["name"])

        # print stats        
        stats_json = df.groupby("status").size().to_dict()
        log.info(json.dumps(stats_json))        

        return df if as_df else df.to_dict("records")

    @overload  # pragma: no cover
    def restore_snapshot(self, before: datetime.datetime = None):
        """Restore the latest snapshot. If timestamp is given, restore the snapshot which was created before or at the given timestamp.

        :param timestamp: The timestamp of the snapshot to restore. If None, the latest snapshot is restored.
        """
        ...

    @overload  # pragma: no cover
    def restore_snapshot(self, snapshot_to_restore: List[dict]|pd.DataFrame):
        """Restore the given snapshot.

        :param snapshot_to_restore: The snapshot to restore.
        """
        ...

    @overload  # pragma: no cover
    def restore_snapshot(self, diff: List[dict]|pd.DataFrame):
        """Restore the left snapshot by applying the given diff to the right snapshot. 

        :param diff: The diff to apply.
        """
        ...

    def restore_snapshot(
        self,
        snapshot_to_restore: List[dict]|pd.DataFrame = None,
        diff: List[dict]|pd.DataFrame = None,
        before: datetime.datetime = None
    ):
        if isinstance(diff, list):
            if len(diff) == 0:
                log.info(f"Diff empty: Nothing to restore")
                return
            diff = pd.DataFrame(diff).set_index("name")
            
        if diff is None:
            if snapshot_to_restore is None:
                snapshot_to_restore = self.read_snapshot(before=before)
            snapshot_to_restore = _convert_snapshot_as_required(snapshot_to_restore, as_df=False)
            current_snapshot, snapshot_dt = self.generate_snapshot()
            diff = self.compare_snapshots(snapshot_to_restore, current_snapshot, as_df=True)

        only_left  = set( diff[ diff["status"] == "only_left"  ].index )
        only_right = set( diff[ diff["status"] == "only_right" ].index )
        different  = set( diff[ diff["status"] == "different"  ].index )

        log.info(f"Copying files: {len(only_left)} only_left + {len(different)} different")
        for file_relative_path in sorted(only_left | different):
            file_info_row = diff.loc[file_relative_path, :]
            md5 = file_info_row["md5_left"]
            heap_file_path_relative = HEAP_FMT_FN(md5)
            heap_file_path = f"{self.heap_root}/{heap_file_path_relative}"
            src_file_path = f"{self.src_root}/{file_relative_path}"
            log.debug(f"Copying '{heap_file_path_relative}' to '{file_relative_path}'")
            self.src_fs.makedirs(os.path.dirname(src_file_path), exist_ok=True)
            with self.heap_fs.open(heap_file_path, "rb") as heap_file:
                with self.src_fs.open(src_file_path, "wb") as src_file:
                    src_file.write(heap_file.read())
        
        log.info(f"Deleting {len(only_right)} files")
        for file_relative_path in sorted(only_right):
            src_file_path = f"{self.src_root}/{file_relative_path}"
            log.debug(f"Deleting '{file_relative_path}'")
            self.src_fs.rm(src_file_path)    
