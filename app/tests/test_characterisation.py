"""Characterisation tests: pin what the app outputs today for recorded API data.

These don't say the numbers are right; test_flips.py and friends do that.
They say the numbers haven't changed. Feed the recorded wiki responses
through PriceCache and the real /api routes, and compare with the snapshots
in fixtures/snapshots/. A refactor (S1: PriceSource) must leave them green.

If a change to the output is intended, regenerate and review the diff:

    OSRS_UPDATE_SNAPSHOTS=1 uv run pytest app/tests/test_characterisation.py
"""

from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from fastapi.testclient import TestClient
from osrs_finance_hub.cache import PriceCache
from osrs_finance_hub.config import Settings
from osrs_finance_hub.positions import PositionStore
from osrs_finance_hub.server import create_app
from wiki_fixtures import RECORDED_AT, FixtureSource

SNAPSHOT_DIR = Path(__file__).parent / "fixtures" / "snapshots"
UPDATE = os.environ.get("OSRS_UPDATE_SNAPSHOTS") == "1"


def load_cache(settings: Settings) -> PriceCache:
    """Fill a PriceCache from the fixtures, without starting the refresh loops."""
    cache = PriceCache(FixtureSource(), settings)

    async def fill() -> None:
        await cache.refresh_mapping()
        await cache.refresh_latest()
        await cache.refresh_window("1h")
        await cache.refresh_window("5m")

    asyncio.run(fill())
    return cache


@pytest.fixture
def client(tmp_path, monkeypatch):
    # Flips include each price's age, so stop the clock at the moment of recording.
    monkeypatch.setattr("osrs_finance_hub.cache.time", SimpleNamespace(time=lambda: RECORDED_AT))
    settings = Settings(contact="test", positions_file=tmp_path / "positions.json")
    app = create_app(settings)
    # The lifespan would start the real wiki client, so it is not run.
    # Its state is filled in by hand, as in test_set_api.py.
    app.state.cache = load_cache(settings)
    app.state.settings = settings
    app.state.positions = PositionStore(settings.positions_file)
    return TestClient(app)


def first_difference(expected: Any, actual: Any, path: str = "") -> str | None:
    """Where two JSON values first differ, so a failure names the field."""
    if type(expected) is not type(actual):
        return f"{path}: {expected!r} != {actual!r}"
    if isinstance(expected, dict):
        for key in sorted(expected.keys() | actual.keys()):
            if key not in expected or key not in actual:
                return f"{path}.{key}: only in {'actual' if key in actual else 'snapshot'}"
            diff = first_difference(expected[key], actual[key], f"{path}.{key}")
            if diff:
                return diff
        return None
    if isinstance(expected, list):
        if len(expected) != len(actual):
            return f"{path}: {len(expected)} items in snapshot, {len(actual)} now"
        for i, (e, a) in enumerate(zip(expected, actual, strict=True)):
            diff = first_difference(e, a, f"{path}[{i}]")
            if diff:
                return diff
        return None
    return None if expected == actual else f"{path}: {expected!r} != {actual!r}"


def check_snapshot(name: str, actual: Any) -> None:
    path = SNAPSHOT_DIR / f"{name}.json"
    if UPDATE:
        SNAPSHOT_DIR.mkdir(exist_ok=True)
        path.write_text(json.dumps(actual, indent=1, sort_keys=True) + "\n")
        return
    expected = json.loads(path.read_text())
    diff = first_difference(expected, actual)
    assert diff is None, f"{name} snapshot changed at {diff}"


def test_flips_unchanged(client):
    body = client.get("/api/flips").json()
    assert len(body["items"]) == len(client.app.state.cache.mapping)
    # The status block holds the server clock and endpoint ages, not flip output.
    check_snapshot("flips", body["items"])


# Hand-checked by Joe against the recording (6 Nov 2026). Spelled out so the
# tax rules are visible, not just buried in the snapshot.
KNOWN_FLIPS = {
    # id: (name, low, high, tax, margin, suggested_buy, suggested_sell)
    22486: ("Scythe of Vitur (uncharged)", 1_027_000_000, 1_041_154_823, 5_000_000, 9_154_823,
            1_028_144_352, 1_040_010_471),                                                 # 5m cap
    20997: ("Twisted bow", 1_414_306_000, 1_423_000_000, 5_000_000, 3_694_000,
            1_414_767_750, 1_422_538_250),                                                 # 5m cap
    23959: ("Enhanced crystal teleport seed", 2_800_336, 2_888_708, 57_774, 30_598, 2_804_160, 2_884_884),  # 2%
}


@pytest.mark.parametrize("item_id", KNOWN_FLIPS)
def test_known_flips(client, item_id):
    name, low, high, tax, margin, buy, sell = KNOWN_FLIPS[item_id]
    flip = next(f for f in client.get("/api/flips").json()["items"] if f["id"] == item_id)
    assert flip["name"] == name
    assert (flip["low"], flip["high"]) == (low, high)
    assert flip["tax"] == tax
    assert flip["margin"] == high - tax - low == margin
    assert (flip["suggested_buy"], flip["suggested_sell"]) == (buy, sell)


def test_whole_hour_spike_is_not_flagged_today(client):
    """Known gap, pinned so it is visible. Not a refactor concern.

    Steel knife(p+) traded at 200 / 90,000 for the whole hour, so the spread
    matches its hourly average and `suspicious_spread` stays false. Joe confirms
    the 90,000 sell price is not real. When the spike check is fixed, flip this.
    """
    flip = next(f for f in client.get("/api/flips").json()["items"] if f["id"] == 5656)
    assert flip["high"] == 90_000
    assert flip["spread_ratio"] == 1.0
    assert flip["suspicious_spread"] is False


def test_tax_exempt_items_are_taxed_today(client):
    """Known gap, pinned so it is visible. Not a refactor concern.

    Old school bonds are exempt from the GE tax in game (as are some food,
    tools, teleports and low-level ammo; see the wiki's Grand Exchange page),
    but ge_tax() has no exemption list and charges 2%. The real bond margin
    here is 500,672, not 290,659. When exemptions are added, flip this.
    """
    flip = next(f for f in client.get("/api/flips").json()["items"] if f["id"] == 13190)
    assert flip["name"] == "Old school bond"
    assert (flip["low"], flip["high"]) == (10_000_000, 10_500_672)
    assert flip["tax"] == 210_013          # should be 0
    assert flip["margin"] == 290_659       # should be 500,672


def test_sets_unchanged(client):
    body = client.get("/api/sets").json()
    assert body["sets"], "fixtures should contain at least one whole set"
    check_snapshot("sets", body["sets"])


def test_magic_unchanged(client):
    body = client.get("/api/magic").json()
    assert body["opportunities"], "fixtures should contain alch or enchant items"
    check_snapshot("magic", body["opportunities"])
