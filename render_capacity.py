from __future__ import annotations

import asyncio
import os
import threading
from collections import OrderedDict
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Callable, Hashable


# Render Free exposes very little CPU. A single CPU-bound chemistry worker gives
# better aggregate throughput and much lower peak memory than allowing RDKit and
# Pillow to fan out across the default asyncio executor.
_WORKERS = max(1, int(os.getenv("CHEM_RENDER_WORKERS", "1")))
_MAX_PENDING = max(_WORKERS, int(os.getenv("CHEM_MAX_PENDING", "48")))
_CACHE_MAX_BYTES = max(0, int(os.getenv("CHEM_RENDER_CACHE_MB", "12"))) * 1024 * 1024
_CACHE_MAX_ITEMS = max(0, int(os.getenv("CHEM_RENDER_CACHE_ITEMS", "48")))

_executor = ThreadPoolExecutor(max_workers=_WORKERS, thread_name_prefix="chem-render")
_pending_lock = threading.Lock()
_pending = 0

_cache_lock = threading.Lock()
_cache: OrderedDict[Hashable, bytes] = OrderedDict()
_cache_bytes = 0


def capacity_snapshot() -> dict[str, int]:
    with _pending_lock:
        pending = _pending
    with _cache_lock:
        items = len(_cache)
        cache_bytes = _cache_bytes
    return {
        "workers": _WORKERS,
        "pending": pending,
        "max_pending": _MAX_PENDING,
        "cache_items": items,
        "cache_bytes": cache_bytes,
    }


def try_reserve() -> bool:
    global _pending
    with _pending_lock:
        if _pending >= _MAX_PENDING:
            return False
        _pending += 1
        return True


def release() -> None:
    global _pending
    with _pending_lock:
        if _pending > 0:
            _pending -= 1


def cache_get(key: Hashable) -> bytes | None:
    if _CACHE_MAX_ITEMS <= 0 or _CACHE_MAX_BYTES <= 0:
        return None
    with _cache_lock:
        value = _cache.get(key)
        if value is None:
            return None
        _cache.move_to_end(key)
        return value


def cache_put(key: Hashable, value: bytes | None) -> None:
    global _cache_bytes
    if not value or _CACHE_MAX_ITEMS <= 0 or _CACHE_MAX_BYTES <= 0:
        return
    size = len(value)
    if size > _CACHE_MAX_BYTES:
        return
    with _cache_lock:
        old = _cache.pop(key, None)
        if old is not None:
            _cache_bytes -= len(old)
        _cache[key] = value
        _cache_bytes += size
        while _cache and (len(_cache) > _CACHE_MAX_ITEMS or _cache_bytes > _CACHE_MAX_BYTES):
            _, evicted = _cache.popitem(last=False)
            _cache_bytes -= len(evicted)


async def run_heavy(func: Callable[..., Any], /, *args, **kwargs) -> Any:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_executor, lambda: func(*args, **kwargs))


async def run_cached_bytes(key: Hashable, func: Callable[..., bytes | None], /, *args, **kwargs) -> bytes | None:
    cached = cache_get(key)
    if cached is not None:
        return cached
    value = await run_heavy(func, *args, **kwargs)
    if isinstance(value, bytes):
        cache_put(key, value)
    return value
