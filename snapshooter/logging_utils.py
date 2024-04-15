from typing import Any, Optional


def log_str(obj: Any, length: Optional[int] = 10_000) -> str:
    s = str(obj)
    if length is not None and len(s) > length:
        s = s[:length - 3] + "..."
    return s
