# Snapshooter (fsspec folder backup and restore tooling)

Provides a set of utilities for diffing and syncing files between two fsspec file systems and performing efficient incremental backups.

## Installation

```bash
pip install snapshooter
```

## Usage

```python
from snapshooter import Snapshotter

# Create a snapshotter object
snapshotter = Snapshooter(
  src_fs=fsspec.filesystem("file"),
  src_root=f"./data/restored",
  snap_fs=fsspec.filesystem("file"),
  snap_root=f"./data/snap",
  heap_fs=fsspec.filesystem("file"),
  heap_root=f"./data/heap",
)

# Generate a snapshot of the current state of the source file system
snapshot, timestamp = snapshooter.make_snapshot()
# As a result, the files are copied from the source file system to the heap file system and the snapshot is created in memory

# Save the snapshot to the snapshot file system
snapshooter._save_snapshot(snapshot, timestamp)

# Restore the snapshot from the snapshot file system to the source file system
restore_snapshooter.restore_snapshot(snapshot)
```

with the following parameters:

- `src_fs`: The file system to be backed up
- `src_root`: The root folder in the source file system to be backed up
- `snap_fs`: The file system to store the snapshots in - the snapshots store the file information as provided by the fsspec file system. Two changes are applied:
  - The file name is changed to be relative to the `src_root` folder
  - An additional `md5` field is added, containing the md5 hash of the file contents. This allows for efficient diffing of files.
- `snap_root`: The root folder in the snapshot file system to store the snapshots in
- `heap_fs`: The file system to store the heap in - the heap stores the file contents into file with the md5 hash of the file as the file name. This allows for efficient deduplication of files.
- `heap_root`: The root folder in the heap file system to store the heap in

## Supported file systems

The current version has been developed and tested with local and azure file systems. If you have a use case for another file system, please look at `fsspec_utils.py / import get_md5_getter`: You will need to implement a new `FSSpecMD5Getter` function for your file system and add it to the `md5_getter_by_fs_protocol` dictionary. Pull requests are welcome.

## About the delta implementation

The `snapshooter.generate_snapshot` tries to avoid copying files from the source file system to the heap file system by checking whether a file with the same md5 hash exists in the heap file system. If a file is found, then the src file is not copied to the heap file system. This allows for efficient incremental backups.

In the azure file system, the md5 hash of the file contents is not always available. In that case, the file is downloaded the first time and the md5 hash is calculated and stored in the snapshot. The subsequent calls to `snapshooter.generate_snapshot` will then use the `etag` attribute of the file (which is always available) and compare it with the value in the previous snapshot: If the `etag` matches, the file is not downloaded and the md5 hash of the previous snapshot is reused. This allows for efficient incremental backups.

In the local file system, the md5 is basically not available. The previous incremental backups is also used here. But instead of the `etag` attribute, the `mtime` attribute is used.
