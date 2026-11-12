"""The cache refreshes lazily and must survive the prices API going down."""

from __future__ import annotations

import asyncio
from collections import Counter
from types import SimpleNamespace

import pytest
from fastapi.testclient import TestClient
from osrs_finance_hub import cache as cache_module
from osrs_finance_hub.cache import ERROR_RETRY_SECONDS, MAPPING_TTL_SECONDS, PriceCache
from osrs_finance_hub.config import Settings
from osrs_finance_hub.positions import PositionStore
from osrs_finance_hub.server import create_app
from osrs_finance_hub.wiki_api import WikiApiError


class FlakyClient:
    """Stand-in for WikiPricesClient that counts calls and can be told to fail."""

    def __init__(self) -> None:
        self.fail = False
        self.calls: Counter[str] = Counter()

    async def _call(self, name: str) -> None:
        self.calls[name] += 1
        # Yield to the event loop, as a real network call would. Without this
        # a refresh finishes before any other request runs, and a concurrency
        # test would pass even with no lock.
        await asyncio.sleep(0)
        if self.fail:
            raise WikiApiError("simulated outage")

    async def fetch_mapping(self):
        await self._call("mapping")
        return [{"id": 1, "name": "Thing", "limit": 100, "icon": "Thing.png"}]

    async def fetch_latest(self):
        await self._call("latest")
        return {"1": {"high": 200, "highTime": 5, "low": 100, "lowTime": 5}}

    async def fetch_average(self, window: str):
        await self._call(window)
        return (
            {"1": {"avgHighPrice": 200, "avgLowPrice": 100,
                   "highPriceVolume": 500, "lowPriceVolume": 500}},
            5,
        )

    async def fetch_timeseries(self, item_id: int, timestep: str):
        await self._call("timeseries")
        return [{"timestamp": 1, "avgHighPrice": 200, "avgLowPrice": 100,
                 "highPriceVolume": 10, "lowPriceVolume": 10}]


class Clock:
    """A clock the test moves by hand."""

    def __init__(self) -> None:
        self.now = 1_000_000.0

    def time(self) -> float:
        return self.now


@pytest.fixture
def clock(monkeypatch) -> Clock:
    fake = Clock()
    monkeypatch.setattr(cache_module, "time", SimpleNamespace(time=fake.time))
    return fake


def make_cache() -> tuple[PriceCache, FlakyClient]:
    client = FlakyClient()
    settings = Settings(contact="test", latest_refresh_seconds=60, hourly_refresh_seconds=300)
    return PriceCache(client, settings), client


ALL_ENDPOINTS = {"mapping": 1, "latest": 1, "1h": 1, "5m": 1}


async def test_start_primes_every_endpoint_once(clock):
    cache, client = make_cache()
    await cache.start()
    assert client.calls == ALL_ENDPOINTS
    assert cache.build_flips()[0].margin == 96  # 200 - 4 tax - 100


async def test_fresh_data_is_not_fetched_again(clock):
    cache, client = make_cache()
    await cache.start()
    clock.now += 59
    await cache.ensure_fresh()
    assert client.calls == ALL_ENDPOINTS


async def test_stale_endpoints_refresh_on_request(clock):
    cache, client = make_cache()
    await cache.start()

    clock.now += 60  # /latest is due, the 300s windows are not
    await cache.ensure_fresh()
    assert client.calls == {"mapping": 1, "latest": 2, "1h": 1, "5m": 1}

    clock.now += 240  # now 300s in: the windows are due too
    await cache.ensure_fresh()
    assert client.calls == {"mapping": 1, "latest": 3, "1h": 2, "5m": 2}

    clock.now += MAPPING_TTL_SECONDS
    await cache.ensure_fresh()
    assert client.calls["mapping"] == 2


async def test_concurrent_requests_fetch_once(clock):
    cache, client = make_cache()
    await asyncio.gather(*(cache.ensure_fresh() for _ in range(10)))
    assert client.calls == ALL_ENDPOINTS


async def test_failed_refresh_keeps_the_previous_prices(clock):
    cache, client = make_cache()
    await cache.start()

    client.fail = True
    clock.now += 300
    await cache.ensure_fresh()

    assert cache.build_flips()[0].margin == 96, "stale data should survive an outage"
    status = cache.status()
    assert status["have_data"] is True
    assert {"latest", "1h", "5m"} <= set(status["errors"])


async def test_an_outage_is_retried_once_per_interval_not_per_request(clock):
    cache, client = make_cache()
    await cache.start()
    client.fail = True
    clock.now += 60
    await cache.ensure_fresh()
    assert client.calls["latest"] == 2

    clock.now += 1
    for _ in range(5):
        await cache.ensure_fresh()
    assert client.calls["latest"] == 2, "must not hammer the wiki while it is down"


async def test_errors_clear_once_the_api_recovers(clock):
    cache, client = make_cache()
    await cache.start()
    client.fail = True
    clock.now += 60
    await cache.ensure_fresh()
    assert "latest" in cache.status()["errors"]

    client.fail = False
    clock.now += 60
    await cache.ensure_fresh()
    assert "latest" not in cache.status()["errors"]


async def test_failed_mapping_retries_soon_not_next_day(clock):
    cache, client = make_cache()
    client.fail = True
    await cache.start()
    client.fail = False

    clock.now += ERROR_RETRY_SECONDS
    await cache.ensure_fresh()
    assert client.calls["mapping"] == 2
    assert cache.mapping


async def test_timeseries_is_cached_between_calls(clock):
    cache, client = make_cache()
    first = await cache.get_timeseries(1, "1h")
    # A second call inside the TTL must not reach the client, so it still
    # works even once the client would fail.
    client.fail = True
    second = await cache.get_timeseries(1, "1h")
    assert first == second


def test_only_api_routes_trigger_a_refresh(tmp_path, clock):
    settings = Settings(contact="test", positions_file=tmp_path / "positions.json")
    app = create_app(settings)
    # Lifespan not run, so nothing is primed: the first /api call must fetch.
    cache, client = make_cache()
    app.state.cache = cache
    app.state.settings = settings
    app.state.positions = PositionStore(settings.positions_file)
    http = TestClient(app)

    assert http.get("/").status_code == 200
    assert http.get("/static/app.js").status_code == 200
    assert not client.calls, "the page and static files must never reach the wiki"

    assert http.get("/api/status").json()["have_data"] is True
    assert client.calls == ALL_ENDPOINTS


def test_health_never_touches_the_wiki(tmp_path, clock):
    settings = Settings(contact="test", positions_file=tmp_path / "positions.json")
    app = create_app(settings)
    cache, client = make_cache()  # never primed: every endpoint is due
    client.fail = True            # and the wiki is down
    app.state.cache = cache
    app.state.settings = settings
    app.state.positions = PositionStore(settings.positions_file)

    response = TestClient(app).get("/health")
    assert response.status_code == 200
    assert response.json()["status"] == "ok"
    assert not client.calls, "a health probe must not fetch, or an outage restarts us"


async def test_an_unexpected_refresh_error_degrades_like_a_wiki_error(clock):
    cache, client = make_cache()
    await cache.start()

    async def broken():
        raise AttributeError("'list' object has no attribute 'get'")

    client.fetch_latest = broken
    clock.now += 60
    await cache.ensure_fresh()  # must not raise, or the /api request would 500

    assert cache.build_flips()[0].margin == 96, "old prices survive"
    assert "unexpected" in cache.status()["errors"]["latest"]
    # And it backs off like any failure: no retry on the very next request.
    calls_before = client.calls["latest"]
    clock.now += 1
    await cache.ensure_fresh()
    assert client.calls["latest"] == calls_before
