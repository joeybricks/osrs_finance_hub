"""The cache must survive the prices API going down."""

from __future__ import annotations

import pytest

from flipfinder.cache import PriceCache
from flipfinder.config import Settings
from flipfinder.wiki_api import WikiApiError


class FlakyClient:
    """Stand-in for WikiPricesClient whose failures we can switch on."""

    def __init__(self) -> None:
        self.fail = False

    def _maybe_fail(self) -> None:
        if self.fail:
            raise WikiApiError("simulated outage")

    async def fetch_mapping(self):
        self._maybe_fail()
        return [{"id": 1, "name": "Thing", "limit": 100, "icon": "Thing.png"}]

    async def fetch_latest(self):
        self._maybe_fail()
        return {"1": {"high": 200, "highTime": 5, "low": 100, "lowTime": 5}}

    async def fetch_average(self, window: str):
        self._maybe_fail()
        return (
            {"1": {"avgHighPrice": 200, "avgLowPrice": 100,
                   "highPriceVolume": 500, "lowPriceVolume": 500}},
            5,
        )

    async def fetch_timeseries(self, item_id: int, timestep: str):
        self._maybe_fail()
        return [{"timestamp": 1, "avgHighPrice": 200, "avgLowPrice": 100,
                 "highPriceVolume": 10, "lowPriceVolume": 10}]


def make_cache() -> tuple[PriceCache, FlakyClient]:
    client = FlakyClient()
    # Long intervals so the background loops never fire during the test.
    settings = Settings(contact="test", latest_refresh_seconds=3600, hourly_refresh_seconds=3600)
    return PriceCache(client, settings), client


@pytest.mark.asyncio
async def test_failed_refresh_keeps_the_previous_prices():
    cache, client = make_cache()
    await cache.start()
    try:
        before = cache.build_flips()
        assert before[0].margin == 96  # 200 - 4 tax - 100

        client.fail = True
        await cache.refresh_latest()
        await cache.refresh_window("1h")

        after = cache.build_flips()
        assert after[0].margin == 96, "stale data should survive an outage"

        status = cache.status()
        assert status["have_data"] is True
        assert "latest" in status["errors"]
        assert "1h" in status["errors"]
    finally:
        await cache.stop()


@pytest.mark.asyncio
async def test_errors_clear_once_the_api_recovers():
    cache, client = make_cache()
    await cache.start()
    try:
        client.fail = True
        await cache.refresh_latest()
        assert cache.status()["errors"]

        client.fail = False
        await cache.refresh_latest()
        assert "latest" not in cache.status()["errors"]
    finally:
        await cache.stop()


@pytest.mark.asyncio
async def test_timeseries_is_cached_between_calls():
    cache, client = make_cache()
    await cache.start()
    try:
        first = await cache.get_timeseries(1, "1h")
        # A second call inside the TTL must not reach the client, so it still
        # works even once the client would fail.
        client.fail = True
        second = await cache.get_timeseries(1, "1h")
        assert first == second
    finally:
        await cache.stop()
