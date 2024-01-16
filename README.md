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
snapshotter = Snapshotter(
    source_fs=fsspec.filesystem("file://path/to/source/folder"),
    source_root="path/to/source/folder",
    target_fs="file://path/to/target/folder",
    snapshot_fs="file://path/to/snapshot/folder",
)
```

## Supported file systems

Developed and tested with local and azure file systems. If you have a use case for another file system, please look at `fsspec_utils.py / import get_md5_getter`: You will need to implement a new `FSSpecMD5Getter` function for your file system and add it to the `md5_getter_by_fs_protocol` dictionary. Pull requests are welcome.

