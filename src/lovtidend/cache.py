"""Simple file-based cache for HTTP responses."""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass
from pathlib import Path


@dataclass(slots=True)
class CachePolicy:
    """Defines how long a cached entry should be kept."""

    namespace: str
    ttl_seconds: float


class ResponseCache:
    """Store small HTTP responses on disk to avoid re-downloading them."""

    def __init__(self, root: Path) -> None:
        self.root = root
        self.root.mkdir(parents=True, exist_ok=True)

    def read(self, policy: CachePolicy, key: str) -> str | None:
        path = self._path(policy, key)
        if policy.ttl_seconds <= 0:
            return None
        try:
            mtime = path.stat().st_mtime
        except FileNotFoundError:
            return None
        except OSError:
            return None
        age = time.time() - mtime
        if age > policy.ttl_seconds:
            return None
        try:
            return path.read_text(encoding="utf-8")
        except OSError:
            return None

    def write(self, policy: CachePolicy, key: str, payload: str) -> None:
        path = self._path(policy, key)
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(payload, encoding="utf-8")
        except OSError:
            # Caching is best-effort; ignore failures so scraping can proceed.
            return

    def _path(self, policy: CachePolicy, key: str) -> Path:
        digest = hashlib.sha1(key.encode("utf-8")).hexdigest()
        filename = f"{digest}.cache"
        return self.root / policy.namespace / filename


__all__ = ["CachePolicy", "ResponseCache"]
