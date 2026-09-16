import asyncio

import render_capacity as capacity


def test_capacity_snapshot_is_bounded():
    snap = capacity.capacity_snapshot()
    assert snap["workers"] >= 1
    assert snap["max_pending"] >= snap["workers"]
    assert snap["pending"] >= 0


def test_render_cache_round_trip():
    key = ("unit", "cache")
    capacity.cache_put(key, b"abc")
    assert capacity.cache_get(key) == b"abc"


def test_run_cached_bytes_avoids_duplicate_work():
    calls = {"n": 0}

    def build():
        calls["n"] += 1
        return b"payload"

    async def run():
        key = ("unit", "dedupe")
        a = await capacity.run_cached_bytes(key, build)
        b = await capacity.run_cached_bytes(key, build)
        return a, b

    a, b = asyncio.run(run())
    assert a == b == b"payload"
    assert calls["n"] == 1
