"""Bounded in-memory store for the ORCA bot's wizard state.

Render's free plan has 512 MB of RAM. The original plain ``dict`` kept one entry
per user forever, so memory grew with every new chat until the service was
restarted. This store keeps the same mapping API the bot already uses
(``get`` / ``[]=`` / ``pop`` / ``in``) but:

* drops sessions that were not written for ``ttl_seconds`` (default 6 hours), and
* keeps at most ``max_items`` sessions, evicting the least recently written one.

Both limits can be overridden with the ``STATE_CACHE_MAX_ITEMS`` and
``STATE_CACHE_TTL_SECONDS`` environment variables.
"""
from __future__ import annotations

import os
import threading
import time
from collections import OrderedDict
from collections.abc import MutableMapping
from typing import Any, Callable, Iterator

DEFAULT_MAX_ITEMS = 500
DEFAULT_TTL_SECONDS = 6 * 60 * 60


def _env_number(name: str, default, cast):
    try:
        value = cast(os.environ.get(name, default))
    except (TypeError, ValueError):
        return default
    return value if value > 0 else default


class BoundedStateCache(MutableMapping):
    def __init__(
        self,
        max_items: int | None = None,
        ttl_seconds: float | None = None,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.max_items = (
            max_items
            if max_items is not None
            else _env_number("STATE_CACHE_MAX_ITEMS", DEFAULT_MAX_ITEMS, int)
        )
        self.ttl_seconds = (
            ttl_seconds
            if ttl_seconds is not None
            else _env_number("STATE_CACHE_TTL_SECONDS", DEFAULT_TTL_SECONDS, float)
        )
        self._clock = clock
        # Oldest write first, so expiry and eviction only look at the front.
        self._data: OrderedDict[str, tuple[float, Any]] = OrderedDict()
        self._lock = threading.RLock()

    def _purge_expired(self, now: float) -> None:
        while self._data:
            key = next(iter(self._data))
            if now - self._data[key][0] <= self.ttl_seconds:
                break
            del self._data[key]

    def __getitem__(self, key: str) -> Any:
        with self._lock:
            stamp, value = self._data[key]
            if self._clock() - stamp > self.ttl_seconds:
                del self._data[key]
                raise KeyError(key)
            return value

    def __setitem__(self, key: str, value: Any) -> None:
        with self._lock:
            now = self._clock()
            self._purge_expired(now)
            self._data.pop(key, None)
            self._data[key] = (now, value)
            while len(self._data) > self.max_items:
                self._data.popitem(last=False)

    def __delitem__(self, key: str) -> None:
        with self._lock:
            del self._data[key]

    def __iter__(self) -> Iterator[str]:
        with self._lock:
            self._purge_expired(self._clock())
            return iter(list(self._data))

    def __len__(self) -> int:
        with self._lock:
            self._purge_expired(self._clock())
            return len(self._data)

    def clear(self) -> None:
        with self._lock:
            self._data.clear()
