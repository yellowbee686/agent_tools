"""Tiny mtime-based cache for parsed session details.

JSONL transcripts can be hundreds of KB; reading them on every page load is
wasteful. Adapters cache parse results keyed by file path and invalidate
when ``mtime`` (or size) changes.
"""

from __future__ import annotations

import threading
from pathlib import Path
from typing import Callable, Generic, TypeVar

T = TypeVar("T")


class MtimeCache(Generic[T]):
    """File-mtime keyed in-memory cache.

    Cheap and thread-safe. ``loader(path)`` is called whenever the cached
    entry is missing or stale.
    """

    def __init__(self, loader: Callable[[Path], T | None]) -> None:
        self._loader = loader
        self._lock = threading.Lock()
        self._store: dict[Path, tuple[float, int, T | None]] = {}

    def get(self, path: Path) -> T | None:
        try:
            stat = path.stat()
        except OSError:
            return None
        key = path
        signature = (stat.st_mtime, stat.st_size)
        with self._lock:
            entry = self._store.get(key)
            if entry and (entry[0], entry[1]) == signature:
                return entry[2]
        value = self._loader(path)
        with self._lock:
            self._store[key] = (signature[0], signature[1], value)
        return value

    def invalidate_all(self) -> None:
        with self._lock:
            self._store.clear()
