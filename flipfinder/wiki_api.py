"""Thin client for the OSRS Wiki real-time prices API.

API etiquette, per the wiki's rules:
  * Always send a descriptive User-Agent with a contact. Never a library default.
  * Only fetch the bulk endpoints (no `id` param) for prices. Never loop over
    thousands of items one request at a time.
  * The only per-item endpoint we touch is /timeseries, on demand, when the
    user opens one item's detail view.
"""

from __future__ import annotations

from typing import Any

import httpx

BASE_URL = "https://prices.runescape.wiki/api/v1/osrs"

VALID_TIMESTEPS = ("5m", "1h", "6h", "24h")


class WikiApiError(RuntimeError):
    """Any failure talking to the prices API."""


class WikiPricesClient:
    """Async client for the five endpoints we use."""

    def __init__(self, user_agent: str, timeout: float = 20.0) -> None:
        self._client = httpx.AsyncClient(
            base_url=BASE_URL,
            headers={"User-Agent": user_agent},
            timeout=timeout,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _get_json(self, path: str, params: dict[str, Any] | None = None) -> Any:
        try:
            response = await self._client.get(path, params=params)
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as exc:
            raise WikiApiError(
                f"{path} returned HTTP {exc.response.status_code}"
            ) from exc
        except httpx.HTTPError as exc:
            raise WikiApiError(f"{path} request failed: {exc}") from exc
        except ValueError as exc:  # malformed JSON
            raise WikiApiError(f"{path} returned invalid JSON: {exc}") from exc

    async def fetch_mapping(self) -> list[dict[str, Any]]:
        """Item metadata. A list, not a dict. Fetched once at startup."""
        data = await self._get_json("/mapping")
        if not isinstance(data, list):
            raise WikiApiError("/mapping did not return a list")
        return data

    async def fetch_latest(self) -> dict[str, dict[str, Any]]:
        """Most recent instant-buy/instant-sell prices, keyed by item id string."""
        payload = await self._get_json("/latest")
        return payload.get("data", {})

    async def fetch_average(self, window: str) -> tuple[dict[str, dict[str, Any]], int | None]:
        """Volume-weighted averages over a window. `window` is "5m" or "1h"."""
        if window not in ("5m", "1h"):
            raise ValueError(f"unsupported window: {window}")
        payload = await self._get_json(f"/{window}")
        return payload.get("data", {}), payload.get("timestamp")

    async def fetch_timeseries(self, item_id: int, timestep: str) -> list[dict[str, Any]]:
        """Up to 300 points of history for one item. On-demand only."""
        if timestep not in VALID_TIMESTEPS:
            raise ValueError(f"timestep must be one of {VALID_TIMESTEPS}")
        payload = await self._get_json(
            "/timeseries", params={"id": item_id, "timestep": timestep}
        )
        return payload.get("data", [])
