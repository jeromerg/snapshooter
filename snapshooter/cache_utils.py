import os
import threading
from typing import Callable, Optional


class UniqueStringCache:
    def __init__(self, md5s_loader: Callable[[], set[str]]):
        self.md5s_loader = md5s_loader
        self.md5s : Optional[set[str]] = None

    def ensure_init(self):
        if self.md5s is None:
            self.md5s = self.md5s_loader()
        return self.md5s
        
    def contains(self, md5: str) -> bool:
        self.ensure_init()
        return md5 in self.md5s
    
    def add(self, md5: str) -> None:
        self.ensure_init()
        self.md5s.add(md5)
        
    def size(self) -> int:
        self.ensure_init()
        return len(self.md5s)


class FileUniqueStringCache(UniqueStringCache):
    def __init__(self, md5s_loader: Callable[[], set[str]], local_file: str):
        super().__init__(md5s_loader)
        self.local_file = local_file
        self.lock = threading.Lock()
        self._are_initial_md5s_loaded = None

    def ensure_init(self):
        # lock free check
        if self.md5s is not None:
            return self.md5s
        
        with self.lock:
            # double check after acquiring lock
            if self.md5s is not None:
                return self.md5s

            # ok, I am the first one to acquire the lock, I get the duty to initialize
            if not os.path.isfile(self.local_file):
                # cache did not pre-exist, load from loader
                self.md5s = self.md5s_loader()
                # additionally save to file too...
                with open(self.local_file, "w") as f:
                    for md5 in self.md5s:
                        f.write(md5 + "\n")        
            else:
                # cache pre-existed, load from file
                with open(self.local_file, "r") as f:
                    self.md5s = set(f.read().splitlines())
        return self.md5s
        
    def add(self, md5: str) -> None:
        self.ensure_init()
        with self.lock:
            size_before = len(self.md5s)
            self.md5s.add(md5)
            size_after = len(self.md5s)
            # if md5 is new, append to file too
            if size_after > size_before:
                with open(self.local_file, "a") as f:
                    f.write(md5 + "\n")
