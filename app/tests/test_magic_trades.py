"""Pinned alching and enchanting trades: the valuation and the routes."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from osrs_finance_hub.config import Settings
from osrs_finance_hub.flips import BUY_DONE, BUY_KEEP, BUY_STOP, BUY_THINNING, BUY_UNKNOWN, build_flip
from osrs_finance_hub.magic import HIGH_ALCH, JEWELLERY, find_recipe, summarise_magic_trade
from osrs_finance_hub.positions import STORE_VERSION, PositionStore
from osrs_finance_hub.server import create_app


def lot(lot_id, quantity, price, sold=False):
    return {"lot_id": lot_id, "quantity": quantity, "price": price, "sold": sold}


# --- valuation ---------------------------------------------------------------

def test_alching_banks_profit_as_you_cast():
    t = summarise_magic_trade(
        HIGH_ALCH, buy_price=900, buy_quantity=100, bought=60, cast=40,
        rune_cost=150, lots=[], alch_value=1200,
    )
    assert t.cost_each == 1050
    assert t.uncast == 20 and t.remaining_to_buy == 40
    assert t.outlay == 900 * 60 + 150 * 40     # runes only for what is cast
    assert t.realised_net == 40 * 150
    assert t.projected_total == 60 * 150
    assert t.buy_advice == BUY_KEEP and "item" in t.advice_reason


def test_alching_at_a_loss_says_stop():
    t = summarise_magic_trade(
        HIGH_ALCH, buy_price=1100, buy_quantity=10, bought=1, cast=0,
        rune_cost=150, lots=[], alch_value=1200,
    )
    assert t.current_profit_each == -50
    assert t.buy_advice == BUY_STOP


def test_cast_cannot_exceed_what_arrived():
    t = summarise_magic_trade(
        HIGH_ALCH, buy_price=1, buy_quantity=5, bought=5, cast=99,
        rune_cost=0, lots=[], alch_value=2,
    )
    assert t.cast == 5 and t.uncast == 0 and t.buy_advice == BUY_DONE


def test_enchanting_sells_in_lots_against_buy_plus_runes():
    t = summarise_magic_trade(
        JEWELLERY, buy_price=1000, buy_quantity=50, bought=50, cast=30,
        rune_cost=100, lots=[lot("a", 10, 2000, sold=True), lot("b", 5, 2100)],
        output_low=1900, output_high=2000, reference_high=2000,
    )
    net = 2000 - 40 - 1100
    assert t.lots[0].net_per_unit == net
    assert t.realised_net == 10 * net
    assert t.listed_net_if_filled == 5 * (2100 - 42 - 1100)
    assert t.units_unlisted == 15          # enchanted, not listed
    assert t.uncast == 20
    assert t.unlisted_value_now == net * 35
    assert t.projected_total == t.realised_net + t.listed_net_if_filled + net * 35


def test_enchant_margin_thinning_is_measured_from_the_snapshot():
    args = dict(buy_price=1000, buy_quantity=50, bought=10, cast=0, rune_cost=100,
                lots=[], reference_high=2000)
    thin = summarise_magic_trade(JEWELLERY, output_low=1300, output_high=1400, **args)
    assert thin.buy_advice == BUY_THINNING


def test_no_buy_price_asks_for_one():
    t = summarise_magic_trade(
        JEWELLERY, buy_price=None, buy_quantity=10, bought=0, cast=0,
        rune_cost=100, lots=[lot("a", 1, 2000)], output_high=2000,
    )
    assert t.buy_advice == BUY_UNKNOWN
    assert t.projected_total is None and t.lots[0].net_per_unit is None


def test_find_recipe():
    assert find_recipe("enchant_1", 1637)[2] == 2550     # Sapphire ring -> Ring of recoil
    assert find_recipe("enchant_1", 1) is None
    assert find_recipe("high_alch", 1)[2] is None
    assert find_recipe("teleport", 1) is None


# --- routes ------------------------------------------------------------------

SAPPHIRE_RING, RECOIL, NATURE, FIRE, COSMIC, WATER = 1637, 2550, 561, 554, 564, 555
ALCH_ITEM = 1333


class StubCache:
    def __init__(self) -> None:
        rows = {
            SAPPHIRE_RING: (900, 950, None),
            RECOIL: (1500, 1600, None),
            ALCH_ITEM: (14000, 14500, 14400),
            NATURE: (100, 110, None), FIRE: (5, 6, None),
            COSMIC: (80, 90, None), WATER: (5, 6, None),
        }
        self.flips = [
            build_flip({"id": i, "name": f"Item {i}", "limit": 1000, "highalch": ha},
                       {"low": lo, "high": hi, "lowTime": 0, "highTime": 0},
                       {}, now=0, volume_fraction=0.1)
            for i, (lo, hi, ha) in rows.items()
        ]

    def build_flips(self):
        return self.flips


@pytest.fixture
def client(tmp_path):
    settings = Settings(positions_file=tmp_path / "positions.json")
    app = create_app(settings)
    app.state.cache = StubCache()
    app.state.settings = settings
    app.state.positions = PositionStore(settings.positions_file)
    return TestClient(app)


def test_enchant_trade_round_trip(client, tmp_path):
    url = f"/api/magic-positions/enchant_1/{SAPPHIRE_RING}"
    r = client.put(url, json={"staff": True})
    assert r.status_code == 200
    body = r.json()
    assert body["position"]["rune_cost"] == 80          # cosmic only, water is free
    assert body["position"]["output_id"] == RECOIL

    r = client.put(f"{url}/trade", json={"buy_price": 900, "buy_quantity": 10,
                                         "bought": 10, "cast": 10})
    assert r.json()["position"]["reference_high"] == 1600

    r = client.post(f"{url}/lots", json={"quantity": 4, "price": 1600})
    lot_id = r.json()["position"]["lots"][0]["lot_id"]
    r = client.put(f"{url}/lots/{lot_id}", json={"sold": True})
    trade = r.json()["trade"]
    assert trade["realised_net"] == 4 * (1600 - 32 - 980)
    assert trade["units_unlisted"] == 6

    listing = client.get("/api/positions").json()
    [entry] = listing["magic_positions"]
    assert entry["kind"] == JEWELLERY and entry["output"]["id"] == RECOIL

    # It survives a restart.
    reloaded = PositionStore(tmp_path / "positions.json")
    assert reloaded.get_magic(f"enchant_1:{SAPPHIRE_RING}").cast == 10
    assert f'"version": {STORE_VERSION}' in (tmp_path / "positions.json").read_text()

    assert client.delete(f"{url}/lots/{lot_id}").status_code == 200
    assert client.delete(url).json()["removed"] is True
    assert client.get("/api/positions").json()["magic_positions"] == []


def test_alch_trade_and_bad_requests(client):
    url = f"/api/magic-positions/high_alch/{ALCH_ITEM}"
    body = client.put(url, json={"staff": False}).json()
    assert body["position"]["rune_cost"] == 100 + 5 * 5   # nature plus five fire
    assert body["alch_value"] == 14400

    trade = client.put(f"{url}/trade", json={"buy_price": 14000, "bought": 3,
                                             "cast": 2}).json()["trade"]
    assert trade["realised_net"] == 2 * (14400 - 14000 - 125)

    # Alchemy has nothing to sell.
    assert client.post(f"{url}/lots", json={"quantity": 1, "price": 1}).status_code == 404
    assert client.put("/api/magic-positions/enchant_1/1", json={}).status_code == 404
    assert client.put("/api/magic-positions/high_alch/999999", json={}).status_code == 404
    assert client.put("/api/magic-positions/high_alch/1/trade", json={}).status_code == 404
