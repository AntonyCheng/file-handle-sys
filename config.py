import os
from pathlib import Path
from typing import Dict, Optional


def _load_env_file(paths=("env", ".env", "env.example")) -> Dict[str, str]:
    env: Dict[str, str] = {}
    for p in paths:
        path = Path(p)
        if not path.exists():
            continue
        try:
            with path.open("r", encoding="utf-8") as f:
                for line in f:
                    s = line.strip()
                    if not s or s.startswith("#"):
                        continue
                    if "=" not in s:
                        continue
                    k, v = s.split("=", 1)
                    k = k.strip()
                    v = v.strip().strip('\'"')
                    env[k] = v
            return env
        except Exception:
            continue
    return env


_ENV = _load_env_file()


def _get_str(key: str, default: Optional[str] = None) -> Optional[str]:
    return _ENV.get(key, os.environ.get(key, default))


def _get_int(key: str, default: int = 0) -> int:
    val = _ENV.get(key, os.environ.get(key))
    if val is None:
        return default
    try:
        return int(val)
    except Exception:
        return default


# Public settings
KK_HOST_PUBLIC: str = _get_str("KK_HOST_PUBLIC", "localhost:8000")
MAX_UPLOAD_SIZE_BYTES: int = _get_int("MAX_UPLOAD_SIZE_BYTES", 100 * 1024 * 1024)
# TEMP_RETENTION_SECONDS removed: uploaded files are kept permanently by default


