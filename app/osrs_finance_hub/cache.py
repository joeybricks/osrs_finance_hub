"""In-memory cache with background refresh.

The whole point: the browser never triggers a wiki request. A background task
refreshes on a timer, and page loads read whatever is in memory. If a refresh
fails we keep the previous data and record the error, so the UI can show stale
numbers with a warning instead of breaking.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

from .config import Settings
from .flips import Flip, build_all_flips
from .wiki_api import WikiApiError, WikiPricesClient

log = logging.getLogger(__name__)


@dataclass
class EndpointState:
    """Freshness bookkeeping for one endpoint."""

    last_success: float | None = None   # unix seconds of the last good fetch
    last_error: str | None = None       # message from the most recent failure
    last_attempt: float | None = None

    def age_seconds(self) -> float | None:
        if self.last_success is None:
            return None
        return time.time() - self.last_success


# How long a timeseries stays fresh, by timestep. A series of daily points
# only gains a new point once a day, so re-fetching it every minute is pure
# waste. These are multiplied by the configured base TTL.
TIMESERIES_TTL_MULTIPLIER = {"5m": 1, "1h": 5, "6h": 30, "24h": 60}


@dataclass
class TimeseriesEntry:
    points: list[dict[str, Any]]
    fetched_at: float


class PriceCache:
    """Holds mapping, /latest, /1h, /5m and on-demand /timeseries."""

    def __init__(self, client: WikiPricesClient, settings: Settings) -> None:
        self._client = client
        self._settings = settings

        self.mapping: list[dict[str, Any]] = []
        self.latest: dict[str, dict[str, Any]] = {}
        self.hourly: dict[str, dict[str, Any]] = {}
        self.five_min: dict[str, dict[str, Any]] = {}
        self.hourly_timestamp: int | None = None

        self.state: dict[str, EndpointState] = {
            "mapping": EndpointState(),
            "latest": EndpointState(),
            "1h": EndpointState(),
            "5m": EndpointState(),
        }

        self._timeseries: dict[tuple[int, str], TimeseriesEntry] = {}
        self._tasks: list[asyncio.Task[None]] = []
        self._lock = asyncio.Lock()

    # --- reads ---------------------------------------------------------------

    def build_flips(self) -> list[Flip]:
        """Compute every item's flip numbers from whatever is cached now."""
        return build_all_flips(
            self.mapping,
            self.latest,
            self.hourly,
            now=int(time.time()),
            volume_fraction=self._settings.volume_fraction,
            aggression=self._settings.aggression,
            min_profit_per_unit=self._settings.min_profit_per_unit,
        )

    def status(self) -> dict[str, Any]:
        """Freshness summary for the UI banner."""
        latest = self.state["latest"]
        hourly = self.state["1h"]
        stale_threshold = self._settings.latest_refresh_seconds * 3
        latest_age = latest.age_seconds()

        return {
            "server_time": int(time.time()),
            "item_count": len(self.mapping),
            "volume_fraction": self._settings.volume_fraction,
            "aggression": self._settings.aggression,
            "trend_timestep": self._settings.trend_timestep,
            "trend_batch_size": self._settings.trend_batch_size,
            "min_profit_per_unit": self._settings.min_profit_per_unit,
            "latest_refresh_seconds": self._settings.latest_refresh_seconds,
            "hourly_refresh_seconds": self._settings.hourly_refresh_seconds,
            "latest_updated": latest.last_success,
            "latest_age_seconds": latest_age,
            "hourly_updated": hourly.last_success,
            "errors": {
                name: s.last_error for name, s in self.state.items() if s.last_error
            },
            "have_data": bool(self.mapping and self.latest),
            "stale": latest_age is not None and latest_age > stale_threshold,
        }

    async def get_timeseries(self, item_id: int, timestep: str) -> list[dict[str, Any]]:
        """Cached per item and timestep, so clicking around does not spam the API."""
        key = (item_id, timestep)
        ttl = self._settings.timeseries_cache_seconds * TIMESERIES_TTL_MULTIPLIER.get(
            timestep, 1
        )
        entry = self._timeseries.get(key)
        if entry and time.time() - entry.fetched_at < ttl:
            return entry.points

        points = await self._client.fetch_timeseries(item_id, timestep)
        self._timeseries[key] = TimeseriesEntry(points=points, fetched_at=time.time())
        return points

    # --- writes --------------------------------------------------------------

    async def refresh_mapping(self) -> None:
        state = self.state["mapping"]
        state.last_attempt = time.time()
        try:
            mapping = await self._client.fetch_mapping()
        except WikiApiError as exc:
            state.last_error = str(exc)
            log.warning("mapping refresh failed: %s", exc)
            return
        async with self._lock:
            self.mapping = mapping
        state.last_success = time.time()
        state.last_error = None
        log.info("loaded %d items from /mapping", len(mapping))

    async def refresh_latest(self) -> None:
        state = self.state["latest"]
        state.last_attempt = time.time()
        try:
            latest = await self._client.fetch_latest()
        except WikiApiError as exc:
            state.last_error = str(exc)
            log.warning("latest refresh failed, keeping stale data: %s", exc)
            return
        async with self._lock:
            self.latest = latest
        state.last_success = time.time()
        state.last_error = None

    async def refresh_window(self, window: str) -> None:
        state = self.state[window]
        state.last_attempt = time.time()
        try:
            data, timestamp = await self._client.fetch_average(window)
        except WikiApiError as exc:
            state.last_error = str(exc)
            log.warning("%s refresh failed, keeping stale data: %s", window, exc)
            return
        async with self._lock:
            if window == "1h":
                self.hourly = data
                self.hourly_timestamp = timestamp
            else:
                self.five_min = data
        state.last_success = time.time()
        state.last_error = None

    # --- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Prime the cache, then run the refresh loops in the background."""
        await self.refresh_mapping()
        await asyncio.gather(
            self.refresh_latest(),
            self.refresh_window("1h"),
            self.refresh_window("5m"),
        )
        self._tasks = [
            asyncio.create_task(self._loop(self._settings.latest_refresh_seconds, self.refresh_latest)),
            asyncio.create_task(self._loop(self._settings.hourly_refresh_seconds, self._refresh_windows)),
        ]

    async def _refresh_windows(self) -> None:
        await asyncio.gather(self.refresh_window("1h"), self.refresh_window("5m"))

    async def _loop(self, interval: int, job: Any) -> None:
        """Run `job` every `interval` seconds until cancelled."""
        while True:
            await asyncio.sleep(interval)
            try:
                await job()
            except asyncio.CancelledError:
                raise
            except Exception:  # a refresh loop must never die
                log.exception("unexpected error in refresh loop")

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        for task in self._tasks:
            try:
                await task
            except asyncio.CancelledError:
                pass
        self._tasks.clear()
