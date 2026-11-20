"""Serve the recorded wiki responses in fixtures/wiki/ to the app, offline.

`FixtureSource` has the same four fetch methods as `WikiPricesClient`, so it
can stand in for it anywhere. See fixtures/record_wiki.py for how the data
was captured.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

WIKI_DIR = Path(__file__).parent / "fixtures" / "wiki"


def _load(name: str) -> Any:
    return json.loads((WIKI_DIR / f"{name}.json").read_text())


RECORDED_AT: int = _load("meta")["recorded_at"]


class FixtureSource:
    """Recorded /mapping, /latest, /1h and /5m. Unwraps them as the real client does."""

    async def fetch_mapping(self) -> list[dict[str, Any]]:
        return _load("mapping")

    async def fetch_latest(self) -> dict[str, dict[str, Any]]:
        return _load("latest").get("data", {})

    async def fetch_average(self, window: str) -> tuple[dict[str, dict[str, Any]], int | None]:
        payload = _load(window)
        return payload.get("data", {}), payload.get("timestamp")

    async def fetch_timeseries(self, item_id: int, timestep: str) -> list[dict[str, Any]]:
        raise NotImplementedError("no timeseries recorded; the scanner does not use it")
