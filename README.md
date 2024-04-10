# Snapshooter (fsspec folder backup and restore tooling)

Snapshooter is a tool to back up and restore a folder in a fsspec file system. It is designed to be used with the 
fsspec file system library, which provides a unified interface to various file systems (e.g. local, azure, s3, ...).

Key features:
- Backup side is decomposed into two parts: The snapshots and the heap
  - Snapshots: The snapshots store the files information as provided by the fsspec file system, with the following transformations: 
    - The metadata object is jsonified
    - The file name of each file is changed to be relative to the `src_root` folder 
    - An additional `md5` field is added, containing the md5 hash of the file contents. This allows for efficient diffing of files.
  - Heap: The heap folder stores the file contents into file with the md5 hash of the file as the file name. This allows 
    for efficient deduplication of files.
- Efficient incremental backups: Only files that are unknown are copied to the heap file system.

## Installation

```bash
pip install snapshooter
```

## Usage with CLI

### make a snapshot

```bash
snapshooter \
  --file-root tests/unit_test_data/sample_src \
  --heap-root tests/temp/sample_heap \
  --snap-root tests/temp/sample_snap \
  make-snapshot
```

### restore the latest snapshot 

```bash
snapshooter \
  --file-root tests/unit_test_data/restored_src \
  --heap-root tests/temp/sample_heap \
  --snap-root tests/temp/sample_snap \
  restore-snapshot
```
  
### restore the latest snapshot before or at a given timestamp

```bash
snapshooter \
  --file-root tests/unit_test_data/restored_src \
  --heap-root tests/temp/sample_heap \
  --snap-root tests/temp/sample_snap \
  restore-snapshot \
  --latest 2021-09-01T00:00:00  
```

### restore a specific snapshot

```bash
snapshooter \
  --file-root tests/unit_test_data/restored_src \
  --heap-root tests/temp/sample_heap \
  --snap-root tests/temp/sample_snap \
  restore-snapshot \
  --path tests/temp/sample_snap/2024/04/2024-04-10_14-30-40_086601Z.jsonl.gz  
```

### list snapshots

```bash
snapshooter \
  --file-root tests/unit_test_data/restored_src \
  --heap-root tests/temp/sample_heap \
  --snap-root tests/temp/sample_snap \
  list-snapshots
```

### compare snapshots

```bash
snapshooter \
  --file-root tests/unit_test_data/sample_src \
  --heap-root tests/temp/sample_heap \
  --snap-root tests/temp/sample_snap \
  compare-snapshots \
  --path1 tests/temp/sample_snap/2024/04/2024-04-10_14-30-40_086601Z.jsonl.gz \
```

Snapshot can be identified by the options `path1`, `latest1`, `path2`, `latest2`, in the same way as in the 
command `restore-snapshot`.
  
### support for storage options (e.g. to pass credentials)

Example: Azure file system, See accepted syntax directly in the adlfs documentation: https://pypi.org/project/adlfs/.

```bash
az login
snapshooter \
  --file-root az://file-container/file-root \
  --heap-root az://heap-container/heap-root \
  --snap-root az://snap-container/snap-root \
  --file-storage-options '{"account_name": "fileaccountname"}' \
  --heap-storage-options '{"account_name": "heapaccountname"}' \
  --snap-storage-options '{"account_name": "snapaccountname"}' \
  make-snapshot
```

## Usage with Python

See the CLI implementation [here](snapshooter/cli.py) for an example of how to use the `Snapshooter` class.

## Supported file systems

The current version has been developed and tested with local and azure file systems. 

For other file systems: a single function needs to be implemented and added to the 
`md5_getter_by_fs_protocol` dictionary in [fsspec_utils.py](snapshooter/fsspec_utils.py). This function takes as input
the current metadata of a file and the latest snapshot and should return the md5 hash of the file contents if it can be
retrieved without downloading the file. If the md5 hash cannot be retrieved, the function should return `None` and the
file will be downloaded.

**Pull requests are welcome.**
