# %%
import logging
import json
import os
from dotenv import load_dotenv
from fsspec import AbstractFileSystem
import fsspec

logging.basicConfig(level=logging.INFO)
logging.getLogger("azure.core.pipeline").setLevel(logging.WARNING)

log = logging.getLogger("notebook")

load_dotenv()

# %%
SRC_STORAGE_OPTIONS  = json.loads(os.getenv("SRC_STORAGE_OPTIONS", "{}"))  
SRC_ROOT             = os.getenv("SRC_ROOT")
HEAP_STORAGE_OPTIONS = json.loads(os.getenv("HEAP_STORAGE_OPTIONS", "{}"))
HEAP_ROOT            = os.getenv("HEAP_ROOT")
SNAP_STORAGE_OPTIONS = json.loads(os.getenv("SNAP_STORAGE_OPTIONS", "{}"))
SNAP_ROOT            = os.getenv("SNAP_ROOT")

# %%
src_protocol  = SRC_ROOT.split("://")[0]
heap_protocol = HEAP_ROOT.split("://")[0]
snap_protocol = SNAP_ROOT.split("://")[0]

src_fs : AbstractFileSystem = fsspec.filesystem(src_protocol, **SRC_STORAGE_OPTIONS)
heap_fs: AbstractFileSystem = fsspec.filesystem(heap_protocol, **HEAP_STORAGE_OPTIONS)
snap_fs: AbstractFileSystem = fsspec.filesystem(snap_protocol, **SNAP_STORAGE_OPTIONS)

log.info(f"{SRC_STORAGE_OPTIONS=}, {SRC_ROOT=}")
log.info(f"{HEAP_STORAGE_OPTIONS=}, {HEAP_ROOT=}")
log.info(f"{SNAP_STORAGE_OPTIONS=}, {SNAP_ROOT=}")

# %% [markdown]
# # THEN BY FOOT

# %%
#raise ValueError("STOP HERE")

# %% [markdown]
# # BACKUP

# %%
fsspec_snapshot.generate_snapshot(SRC_ROOT, src_fs, SNAP_ROOT, snap_fs, HEAP_ROOT, heap_fs)

# %% [markdown]
# # RESTORE

# %%
#fsspec_snapshot.restore_snapshot(SRC_ROOT, src_fs, SNAP_ROOT, snap_fs, HEAP_ROOT, heap_fs)

# %% COMPARE
snapshot1 = read_snapshot(SRC_ROOT, src_fs, SNAP_ROOT, snap_fs, HEAP_ROOT, heap_fs)
snapshot0 = read_snapshot(SRC_ROOT, src_fs, SNAP_ROOT, snap_fs, HEAP_ROOT, heap_fs)




