import re
from typing import Any, Optional


REGEX_TOKEN_NAMES = re.compile(r"(cred|key|pass|secret|token)", re.IGNORECASE)


def log_str(obj: Any, length: Optional[int] = 10_000) -> str:
    s = str(obj)
    if length is not None and len(s) > length:
        s = s[:length - 3] + "..."
    return s


def safe_log_key_value_str(
    key: str, 
    value: Any, 
    key_length : Optional[int] = 30,
    value_length: Optional[int] = 30
) -> str:
    key_lower = key.lower()
    key_str   = log_str(key  , length=key_length)
    val_str   = log_str(value, length=value_length)
    val_str   = "***" if REGEX_TOKEN_NAMES.search(key_lower) else val_str
    return f"{key_str}={val_str}"
