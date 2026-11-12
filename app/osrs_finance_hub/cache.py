"""In-memory cache that refreshes lazily, when a request finds it stale.

Each bulk endpoint has a time to live (TTL). An /api request calls
`ensure_fresh()` first; any endpoint whose TTL has passed is fetched once,
however many requests arrive together. Nothing is fetched while nobody is
using the app, which suits Cloud Run's request-based billing (CPU is throttled
between requests, so timer loops would stall) and is gentler on the wiki.

If a refresh fails we keep the previous data and record the error, so the UI
can show stale numbers with a warning instead of breaking.
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from .config import Settings
from .flips import Flip, build_all_flips
from .price_source import PriceSource
from .wiki_api import WikiApiError

log = logging.getLogger(__name__)


@dataclass
class EndpointState:
    """Freshness bookkeeping for one endpoint."""

    last_success: float | None = None   # unix seconds of the last good fetch
    last_error: str | None = None       # message from the most recent failure
    last_attempt: float | None = None   # when the most recent fetch finished

    def age_seconds(self) -> float | None:
        if self.last_success is None:
            return None
        return time.time() - self.last_success


# How long a timeseries stays fresh, by timestep. A series of daily points
# only gains a new point once a day, so re-fetching it every minute is pure
# waste. These are multiplied by the configured base TTL.
TIMESERIES_TTL_MULTIPLIER = {"5m": 1, "1h": 5, "6h": 30, "24h": 60}

# The item list only changes when the game updates, so once a day is plenty.
MAPPING_TTL_SECONDS = 24 * 60 * 60

# After a failed refresh, try again no sooner than this (or the TTL, if
# shorter). Stops a failed daily /mapping fetch from waiting a whole day.
ERROR_RETRY_SECONDS = 60


@dataclass
class TimeseriesEntry:
    points: list[dict[str, Any]]
    fetched_at: float


class PriceCache:
    """Holds mapping, /latest, /1h, /5m and on-demand /timeseries."""

    def __init__(self, client: PriceSource, settings: Settings) -> None:
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
        self._lock = asyncio.Lock()
        # One lock per endpoint, so a burst of requests fetches each only once.
        self._refresh_locks = {name: asyncio.Lock() for name in self.state}

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
        try:
            mapping = await self._client.fetch_mapping()
        except WikiApiError as exc:
            state.last_error = str(exc)
            log.warning("mapping refresh failed: %s", exc)
            return
        finally:
            # Stamped when the fetch ends, so requests arriving mid-fetch see
            # it as due, queue on the lock, and get the fresh data.
            state.last_attempt = time.time()
        async with self._lock:
            self.mapping = mapping
        state.last_success = time.time()
        state.last_error = None
        log.info("loaded %d items from /mapping", len(mapping))

    async def refresh_latest(self) -> None:
        state = self.state["latest"]
        try:
            latest = await self._client.fetch_latest()
        except WikiApiError as exc:
            state.last_error = str(exc)
            log.warning("latest refresh failed, keeping stale data: %s", exc)
            return
        finally:
            # Stamped when the fetch ends, so requests arriving mid-fetch see
            # it as due, queue on the lock, and get the fresh data.
            state.last_attempt = time.time()
        async with self._lock:
            self.latest = latest
        state.last_success = time.time()
        state.last_error = None

    async def refresh_window(self, window: str) -> None:
        state = self.state[window]
        try:
            data, timestamp = await self._client.fetch_average(window)
        except WikiApiError as exc:
            state.last_error = str(exc)
            log.warning("%s refresh failed, keeping stale data: %s", window, exc)
            return
        finally:
            # Stamped when the fetch ends, so requests arriving mid-fetch see
            # it as due, queue on the lock, and get the fresh data.
            state.last_attempt = time.time()
        async with self._lock:
            if window == "1h":
                self.hourly = data
                self.hourly_timestamp = timestamp
            else:
                self.five_min = data
        state.last_success = time.time()
        state.last_error = None

    # --- lazy refresh ---------------------------------------------------------

    def _ttl(self, name: str) -> int:
        if name == "mapping":
            return MAPPING_TTL_SECONDS
        if name == "latest":
            return self._settings.latest_refresh_seconds
        return self._settings.hourly_refresh_seconds

    def _is_due(self, name: str) -> bool:
        """Due once the TTL has passed since the last attempt.

        Measured from the attempt, not the last success, so during a wiki
        outage we retry once per TTL rather than once per request.
        """
        state = self.state[name]
        if state.last_attempt is None:
            return True
        wait = self._ttl(name)
        if state.last_error is not None:
            wait = min(wait, ERROR_RETRY_SECONDS)
        return time.time() - state.last_attempt >= wait

    async def _refresh(self, name: str) -> None:
        if name == "mapping":
            await self.refresh_mapping()
        elif name == "latest":
            await self.refresh_latest()
        else:
            await self.refresh_window(name)

    async def _refresh_if_due(self, name: str) -> None:
        if not self._is_due(name):
            return
        async with self._refresh_locks[name]:
            # Check again: a request that held the lock before us may have
            # just refreshed it (double-checked locking).
            if self._is_due(name):
                await self._refresh(name)

    async def ensure_fresh(self) -> None:
        """Refresh every endpoint whose TTL has passed, in parallel."""
        await asyncio.gather(*(self._refresh_if_due(name) for name in self.state))

    # --- lifecycle -----------------------------------------------------------

    async def start(self) -> None:
        """Prime the cache so the first page load has data."""
        await self.ensure_fresh()

    async def stop(self) -> None:
        """Nothing runs in the background any more; kept for the lifespan."""
