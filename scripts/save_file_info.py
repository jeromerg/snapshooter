# %%
import logging
import json
import os
from dotenv import load_dotenv
from fsspec import AbstractFileSystem
import fsspec
import pickle

logging.basicConfig(level=logging.INFO)
logging.getLogger("azure.core.pipeline").setLevel(logging.WARNING)

log = logging.getLogger("notebook")

load_dotenv()

# %%
SRC_STORAGE_OPTIONS  = json.loads(os.getenv("SRC_STORAGE_OPTIONS", "{}"))  
SRC_ROOT             = os.getenv("SRC_ROOT")

# %%
src_protocol  = SRC_ROOT.split("://")[0]

src_fs : AbstractFileSystem = fsspec.filesystem(src_protocol, **SRC_STORAGE_OPTIONS)

log.info(f"{SRC_STORAGE_OPTIONS=}, {SRC_ROOT=}")

# %%
file_infos = src_fs.find(SRC_ROOT, detail=True)

# %%
# pickle file_infos
with open("file_infos.pkl", "wb") as f:
    pickle.dump(file_infos, f)

# %% [markdown]
# # RESTORE

# %%
#fsspec_snapshot.restore_snapshot(SRC_ROOT, src_fs, SNAP_ROOT, snap_fs, HEAP_ROOT, heap_fs)

# %% COMPARE
snapshot1 = read_snapshot(SRC_ROOT, src_fs, SNAP_ROOT, snap_fs, HEAP_ROOT, heap_fs)
snapshot0 = read_snapshot(SRC_ROOT, src_fs, SNAP_ROOT, snap_fs, HEAP_ROOT, heap_fs)




