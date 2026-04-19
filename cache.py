"""File-based cache for queries, web fetches, and satellite images."""

import json
import hashlib
from pathlib import Path

CACHE_DIR = Path("cache")
QUERY_CACHE = CACHE_DIR / "queries"
WEB_CACHE = CACHE_DIR / "web"
IMAGE_CACHE = CACHE_DIR / "images"


def ensure_cache_dirs():
    for d in [QUERY_CACHE, WEB_CACHE, IMAGE_CACHE]:
        d.mkdir(parents=True, exist_ok=True)


class FileCache:
    def __init__(self, directory: Path, extension: str = ".json"):
        self.dir = directory
        self.ext = extension
        self.dir.mkdir(parents=True, exist_ok=True)

    def _path(self, key: str) -> Path:
        safe = hashlib.md5(key.encode()).hexdigest()[:16] if "/" in key or len(key) > 64 else key
        return self.dir / f"{safe}{self.ext}"

    def get(self, key: str):
        p = self._path(key)
        if not p.exists():
            return None
        try:
            if self.ext == ".json":
                return json.loads(p.read_text())
            return p.read_bytes()
        except Exception:
            return None

    def set(self, key: str, value):
        p = self._path(key)
        if self.ext == ".json":
            p.write_text(json.dumps(value, indent=2))
        else:
            p.write_bytes(value)

    def has(self, key: str) -> bool:
        return self._path(key).exists()


query_cache = FileCache(QUERY_CACHE, ".json")
web_cache = FileCache(WEB_CACHE, ".json")
image_cache = FileCache(IMAGE_CACHE, ".jpg")
