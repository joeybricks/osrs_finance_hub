"""Where PriceCache gets its prices from.

`PriceSource` is the contract: anything with these four methods can feed the
cache. Today that is `WikiPricesClient` (live wiki API) in the app and
`FixtureSource` (recorded responses) in the tests. A BigQuery-backed source
could be added later without touching the cache.

It is a Protocol, so a source only has to have the methods; it does not need
to inherit from anything. The signatures match `WikiPricesClient` exactly.

Contract: a failed fetch raises `WikiApiError`. The cache catches that,
keeps its last good data and shows the stale-data banner.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class PriceSource(Protocol):
    async def fetch_mapping(self) -> list[dict[str, Any]]:
        """Item metadata, one dict per item."""
        ...

    async def fetch_latest(self) -> dict[str, dict[str, Any]]:
        """Latest instant-buy/instant-sell prices, keyed by item id string."""
        ...

    async def fetch_average(self, window: str) -> tuple[dict[str, dict[str, Any]], int | None]:
        """Volume-weighted averages for "5m" or "1h", and the window's timestamp."""
        ...

    async def fetch_timeseries(self, item_id: int, timestep: str) -> list[dict[str, Any]]:
        """History for one item. On demand only, never in a loop over items."""
        ...
