"""Unit tests for item set arbitrage."""

from __future__ import annotations

import json
import logging

from flipfinder.flips import build_flip, ge_tax
from flipfinder.set_data import ITEM_SETS
from flipfinder.sets import PACK, UNPACK, build_set_flips

NOW = 1_000_000
FRACTION = 0.1


def flip(item_id, low, high, *, limit=100, buy_vol=1000, sell_vol=1000, name=None):
    """A Flip built the same way the cache builds one.

    `buy_vol` is instant-sell traffic (fills our buys), `sell_vol` is
    instant-buy traffic (fills our sells), matching the API's naming.
    """
    item = {"id": item_id, "name": name or f"Item {item_id}", "limit": limit,
            "members": True, "icon": ""}
    latest = {} if low is None else {"low": low, "high": high,
                                     "lowTime": NOW - 30, "highTime": NOW - 60}
    hourly = {"lowPriceVolume": buy_vol, "highPriceVolume": sell_vol,
              "avgLowPrice": low, "avgHighPrice": high}
    return build_flip(item, latest, hourly, now=NOW, volume_fraction=FRACTION)


SET = (("Test", (10, "Test set"), ((1, "Helm"), (2, "Body"), (3, "Legs"))),)


def build(*flips):
    return build_set_flips({f.id: f for f in flips}, FRACTION, item_sets=SET)


def test_unpack_buys_the_set_low_and_sells_each_piece_high_after_tax():
    [s] = build(flip(10, 1000, 1100),
                flip(1, 300, 400), flip(2, 500, 600), flip(3, 200, 300))
    t = s.unpack
    assert t.cost == 1000
    assert t.tax == ge_tax(400) + ge_tax(600) + ge_tax(300)   # taxed piece by piece
    assert t.proceeds == 1300 - t.tax
    assert t.profit == 1300 - t.tax - 1000
    # Crossing the spread: buy the set at high, dump pieces at low.
    assert t.instant_profit == (300 - 6) + (500 - 10) + (200 - 4) - 1100


def test_pack_buys_pieces_low_and_sells_the_set_high_after_tax():
    [s] = build(flip(10, 1000, 1500),
                flip(1, 300, 400), flip(2, 500, 600), flip(3, 200, 300))
    t = s.pack
    assert t.cost == 1000
    assert t.tax == ge_tax(1500)
    assert t.profit == 1500 - 30 - 1000
    assert t.instant_profit == 1000 - 20 - 1300
    assert s.best().direction == PACK


def test_each_piece_pays_its_own_tax_cap():
    """Selling three 300m pieces pays three capped taxes, not one."""
    big = 300_000_000
    [s] = build(flip(10, 800_000_000, 810_000_000),
                flip(1, big, big), flip(2, big, big), flip(3, big, big))
    assert s.unpack.tax == 3 * 5_000_000


def test_thinnest_piece_caps_the_units_and_is_named():
    [s] = build(flip(10, 1000, 1100, limit=None),
                flip(1, 300, 400, sell_vol=50, name="Helm"),
                flip(2, 500, 600), flip(3, 200, 300))
    assert s.unpack.est_units_per_4h == int(50 * 4 * FRACTION)
    assert s.unpack.bottleneck == "Helm"
    assert s.unpack.est_profit_per_4h == s.unpack.profit * 20


def test_buy_limit_caps_units_when_tighter_than_volume():
    [s] = build(flip(10, 1000, 1100, limit=8),
                flip(1, 300, 400, limit=3), flip(2, 500, 600), flip(3, 200, 300))
    assert s.unpack.est_units_per_4h == 8          # the set's own limit
    assert s.unpack.bottleneck == "buy limit"
    assert s.pack.est_units_per_4h == 3            # tightest piece limit
    assert s.pack.capital_required == 3 * s.pack.cost


def test_fill_time_uses_the_right_volume_for_each_side():
    # Unpack buys the set (filled by its instant-sells) and sells the pieces
    # (filled by their instant-buys).
    [s] = build(flip(10, 1000, 1100, limit=10, buy_vol=100),
                flip(1, 300, 400, sell_vol=200), flip(2, 500, 600), flip(3, 200, 300))
    assert s.unpack.est_hours_to_buy == 10 / (100 * FRACTION)
    assert s.unpack.est_hours_to_sell == 10 / (200 * FRACTION)
    assert s.unpack.est_hours_total == s.unpack.est_hours_to_buy + s.unpack.est_hours_to_sell


def test_a_piece_without_prices_leaves_both_directions_blank():
    [s] = build(flip(10, 1000, 1100), flip(1, None, None),
                flip(2, 500, 600), flip(3, 200, 300))
    assert s.unpack.profit is None and s.pack.profit is None
    assert s.unpack.direction == UNPACK and s.pack.direction == PACK
    assert s.price_age_seconds is None


def test_a_set_whose_items_are_missing_from_mapping_is_skipped(caplog):
    with caplog.at_level(logging.WARNING):
        assert build(flip(10, 1000, 1100), flip(1, 300, 400)) == []
    assert "Test set" in caplog.text


def test_price_age_is_the_stalest_item_and_spikes_propagate():
    stale = flip(2, 500, 600)
    stale.price_age_seconds = 900
    stale.suspicious_spread = True
    [s] = build(flip(10, 1000, 1100), flip(1, 300, 400), stale, flip(3, 200, 300))
    assert s.price_age_seconds == 900
    assert s.suspicious_spread


def test_sets_rank_by_the_better_direction():
    sets = (
        ("T", (10, "Small"), ((1, "a"),)),
        ("T", (20, "Big"), ((2, "b"),)),
    )
    flips = [flip(10, 100, 110), flip(1, 115, 120),          # unpack: small win
             flip(20, 100, 500), flip(2, 50, 60)]            # pack: big win
    ranked = build_set_flips({f.id: f for f in flips}, FRACTION, item_sets=sets)
    assert [s.name for s in ranked] == ["Item 20", "Item 10"]
    assert ranked[0].to_dict()["best_direction"] == PACK
    json.dumps(ranked[0].to_dict())   # the endpoint must be able to serialise it


def test_set_table_is_well_formed():
    set_ids = [set_id for _, (set_id, _), _ in ITEM_SETS]
    assert len(set_ids) == len(set(set_ids)), "a set is listed twice"
    for _, (set_id, name), pieces in ITEM_SETS:
        ids = [cid for cid, _ in pieces]
        assert len(pieces) >= 2, name
        assert len(ids) == len(set(ids)), f"{name} lists a piece twice"
        assert set_id not in ids, f"{name} contains itself"


# --- live set trades ---------------------------------------------------------

from flipfinder.flips import BUY_KEEP, BUY_STOP, BUY_THINNING, BUY_UNKNOWN  # noqa: E402
from flipfinder.sets import set_legs, split_set_cost, summarise_set_trade  # noqa: E402


def buy(item_id, price, ordered, arrived):
    return {"item_id": item_id, "buy_price": price, "buy_quantity": ordered, "bought": arrived}


def lot(lot_id, quantity, price, sold=False):
    return {"lot_id": lot_id, "quantity": quantity, "price": price, "sold": sold}


def test_split_set_cost_follows_the_weights_and_adds_up_exactly():
    shares = split_set_cost(1000, [1, 2, 3], {1: 100, 2: 200, 3: 300})
    assert sum(shares.values()) == 1000
    assert shares[1] < shares[2] < shares[3]
    assert split_set_cost(10, [1, 2, 3], {1: None, 2: 5, 3: 5}) in (
        {1: 3, 2: 3, 3: 4}, {1: 4, 2: 3, 3: 3}, {1: 3, 2: 4, 3: 3})


def test_pack_complete_sets_are_the_piece_you_have_least_of():
    t = summarise_set_trade(
        "pack",
        buys=[buy(1, 100, 10, 6), buy(2, 200, 10, 4), buy(3, 300, 10, 9)],
        sells=[{"item_id": 10, "lots": [lot("a", 3, 700, sold=True)]}],
        prices={1: (100, 110), 2: (200, 210), 3: (300, 310), 10: (680, 700)},
    )
    assert t.complete_sets == 4
    assert [b.spare for b in t.buys] == [2, 0, 5]
    assert t.cost_per_set == 600
    assert t.outlay == 100 * 6 + 200 * 4 + 300 * 9
    sell = t.sells[0]
    assert (sell.units_sold, sell.units_listed, sell.units_unlisted) == (3, 0, 1)
    assert t.realised_net == 3 * (700 - 14 - 600)
    assert t.unlisted_value_now == 1 * (700 - 14 - 600)
    assert t.projected_total == t.realised_net + t.unlisted_value_now
    assert t.remaining_to_buy == 6           # the piece furthest behind
    assert t.current_profit_per_set == 700 - 14 - 600
    assert t.buy_advice == BUY_KEEP and "a set" in t.advice_reason


def test_unpack_splits_the_set_cost_across_the_pieces():
    t = summarise_set_trade(
        "unpack",
        buys=[buy(10, 600, 5, 5)],
        sells=[
            {"item_id": 1, "lots": [lot("a", 5, 150, sold=True)]},
            {"item_id": 2, "lots": [lot("b", 2, 500)]},
        ],
        prices={10: (600, 620), 1: (140, 150), 2: (480, 500)},
        reference_highs={1: 150, 2: 450},
    )
    costs = {s.item_id: s.cost_basis for s in t.sells}
    assert costs == {1: 150, 2: 450}          # 1:3 by the snapshot, adds to 600
    assert t.sells[0].lots[0].net_per_unit == 150 - 3 - 150
    assert t.sells[1].units_unlisted == 3
    # Whole-trade profit does not depend on how the cost was split.
    whole = 5 * (150 - 3) + 2 * (500 - 10) + 3 * (500 - 10) - 5 * 600
    assert t.projected_total == whole


def test_profit_waits_until_every_buy_price_is_known():
    t = summarise_set_trade(
        "pack",
        buys=[buy(1, 100, 5, 5), buy(2, None, 5, 5)],
        sells=[{"item_id": 10, "lots": [lot("a", 5, 700)]}],
        prices={1: (100, 110), 2: (200, 210), 10: (680, 700)},
    )
    assert t.cost_per_set is None
    assert t.projected_total is None and t.realised_net is None
    assert t.sells[0].lots[0].net_per_unit is None
    assert t.sells[0].lots[0].tax_per_unit == 14     # the price side still shows


def test_unknown_cost_with_buying_left_asks_for_prices():
    t = summarise_set_trade(
        "pack", buys=[buy(1, None, 5, 0)], sells=[{"item_id": 10, "lots": []}],
        prices={1: (1, 2), 10: (5, 6)},
    )
    assert t.buy_advice == BUY_UNKNOWN


def test_set_trade_advice_thins_and_stops_against_the_snapshot():
    args = dict(
        direction="pack",
        buys=[buy(1, 100, 10, 2), buy(2, 200, 10, 2)],
        sells=[{"item_id": 10, "lots": []}],
        reference_highs={10: 500},              # opened at 500 - 10 - 300 = 190 a set
    )
    thin = summarise_set_trade(prices={10: (300, 360)}, **args)    # 360 - 7 - 300 = 53
    assert thin.reference_profit_per_set == 190
    assert thin.buy_advice == BUY_THINNING
    stop = summarise_set_trade(prices={10: (300, 305)}, **args)    # 305 - 6 - 300 < 0
    assert stop.buy_advice == BUY_STOP and "Every further set" in stop.advice_reason


def test_set_legs_put_each_side_the_right_way_round():
    sets = (("T", (10, "S"), ((1, "a"), (2, "b"))),)
    assert set_legs(10, "pack", sets) == ([1, 2], [10])
    assert set_legs(10, "unpack", sets) == ([10], [1, 2])
    assert set_legs(10, "sideways", sets) is None
    assert set_legs(99, "pack", sets) is None
