import pytest

from bounded_state_cache import BoundedStateCache, _env_number


class FakeClock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_evicts_least_recently_written_when_full():
    cache = BoundedStateCache(max_items=3, ttl_seconds=100, clock=FakeClock())
    for i in range(5):
        cache[f"user:{i}"] = {"n": i}
    assert list(cache) == ["user:2", "user:3", "user:4"]
    assert "user:0" not in cache


def test_rewriting_a_session_keeps_it_alive_and_newest():
    clock = FakeClock()
    cache = BoundedStateCache(max_items=2, ttl_seconds=100, clock=clock)
    cache["a"] = {"v": 1}
    cache["b"] = {"v": 1}
    cache["a"] = {"v": 2}  # active user writes again
    cache["c"] = {"v": 1}  # pushes out the idle one ("b")
    assert "b" not in cache
    assert cache["a"] == {"v": 2} and "c" in cache


def test_sessions_expire_after_ttl():
    clock = FakeClock()
    cache = BoundedStateCache(max_items=10, ttl_seconds=60, clock=clock)
    cache["old"] = {"v": 1}
    clock.now += 30
    cache["new"] = {"v": 2}
    clock.now += 40  # "old" is 70 s idle, "new" is 40 s
    assert cache.get("old", {}) == {}
    assert "old" not in cache
    assert cache["new"] == {"v": 2}
    assert len(cache) == 1
    clock.now += 100
    assert len(cache) == 0


def test_supports_exactly_the_calls_the_bot_makes():
    cache = BoundedStateCache(max_items=5, ttl_seconds=60, clock=FakeClock())
    assert cache.get("user:1", {}).copy() == {}
    cache["user:1"] = {"coords": "C 0 0 0"}.copy()
    assert cache.get("user:1", {}).copy() == {"coords": "C 0 0 0"}
    assert cache["user:1"]["coords"] == "C 0 0 0"
    assert cache.pop("user:1", None) == {"coords": "C 0 0 0"}
    assert cache.pop("user:1", None) is None
    with pytest.raises(KeyError):
        cache["user:1"]


def test_env_numbers_fall_back_to_defaults(monkeypatch):
    monkeypatch.setenv("STATE_CACHE_MAX_ITEMS", "abc")
    assert _env_number("STATE_CACHE_MAX_ITEMS", 500, int) == 500
    monkeypatch.setenv("STATE_CACHE_MAX_ITEMS", "-3")
    assert _env_number("STATE_CACHE_MAX_ITEMS", 500, int) == 500
    monkeypatch.setenv("STATE_CACHE_MAX_ITEMS", "40")
    assert _env_number("STATE_CACHE_MAX_ITEMS", 500, int) == 40


def test_orca_bot_uses_the_bounded_store():
    import legacy_app

    assert isinstance(legacy_app._state_cache, BoundedStateCache)
    assert legacy_app._state_cache.max_items == 128
    assert legacy_app._state_cache.ttl_seconds == 30 * 60


def test_periodic_sweep_removes_expired_data():
    clock = FakeClock()
    cache = BoundedStateCache(max_items=5, ttl_seconds=1800, clock=clock)
    cache["u"] = {"coords": "H 0 0 0"}
    clock.now += 1801
    assert cache.sweep() == 1
    assert len(cache) == 0
