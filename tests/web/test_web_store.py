from __future__ import annotations

import pytest

from gradmarket.web.queries import Stats
from gradmarket.web.store import Snapshot, SnapshotCache


class FakeClock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t


class CountingLoader:
    def __init__(self) -> None:
        self.calls = 0
        self.fail = False

    def __call__(self) -> Snapshot:
        self.calls += 1
        if self.fail:
            raise RuntimeError("db down")
        return Snapshot(roles=[], stats=Stats(postings_collected=self.calls, collecting_since=None), boards_tracked=1)


def _cache() -> tuple[SnapshotCache, CountingLoader, FakeClock]:
    loader, clock = CountingLoader(), FakeClock()
    return SnapshotCache(loader, ttl=300, retry_backoff=30, clock=clock), loader, clock


def test_reads_within_ttl_hit_cache_not_db():
    cache, loader, clock = _cache()

    cache.get()
    clock.t += 299
    cache.get()

    assert loader.calls == 1
    assert cache.age() == 299


def test_read_after_ttl_refreshes():
    cache, loader, clock = _cache()

    cache.get()
    clock.t += 300
    snapshot = cache.get()

    assert loader.calls == 2
    assert snapshot.stats.postings_collected == 2
    assert cache.age() == 0


def test_failed_refresh_serves_stale_and_backs_off():
    cache, loader, clock = _cache()
    cache.get()
    loader.fail = True

    clock.t += 300
    stale = cache.get()
    clock.t += 29
    cache.get()

    assert stale.stats.postings_collected == 1
    assert loader.calls == 2  # second read within backoff didn't retry

    clock.t += 1
    loader.fail = False
    assert cache.get().stats.postings_collected == 3


def test_first_load_failure_raises():
    cache, loader, _ = _cache()
    loader.fail = True

    with pytest.raises(RuntimeError):
        cache.get()
    assert cache.age() is None
