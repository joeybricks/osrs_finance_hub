"""Unit tests for alchemy and enchanting."""

from __future__ import annotations

from flipfinder.flips import build_flip, ge_tax
from flipfinder.magic import (
    BOLTS,
    HIGH_ALCH,
    JEWELLERY,
    LOW_ALCH,
    build_magic_opportunities,
    items_per_hour,
    rune_cost,
)
from flipfinder.magic_data import (
    BOLT_ENCHANTS,
    ELEMENTAL_RUNES,
    JEWELLERY_ENCHANTS,
    RUNE_IDS,
    SPELLS,
)

NOW = 1_000_000
FRACTION = 0.1


def flip(item_id, low, high, *, highalch=None, lowalch=None, limit=10_000,
         buy_vol=100_000, sell_vol=100_000, members=False, name=None):
    item = {"id": item_id, "name": name or f"Item {item_id}", "limit": limit,
            "highalch": highalch, "lowalch": lowalch, "members": members}
    latest = {} if low is None else {"low": low, "high": high,
                                     "lowTime": NOW - 10, "highTime": NOW - 10}
    hourly = {"lowPriceVolume": buy_vol, "highPriceVolume": sell_vol}
    return build_flip(item, latest, hourly, now=NOW, volume_fraction=FRACTION)


def runes(low=100, high=110):
    """Every rune at the same price, so costs are easy to check by hand."""
    return {rid: flip(rid, low, high, name=f"{name} rune") for name, rid in RUNE_IDS.items()}


def by_id(*flips, rune_low=100, rune_high=110):
    out = runes(rune_low, rune_high)
    out.update({f.id: f for f in flips})
    return out


def only(rows, kind, input_id):
    [row] = [r for r in rows if r.kind == kind and r.input_id == input_id]
    return row


# --- data ----------------------------------------------------------------------

def test_spell_table_is_well_formed():
    for key, spell in SPELLS.items():
        assert spell.key == key
        assert 1 <= spell.level <= 99 and spell.ticks >= 1 and spell.exp > 0
        assert all(rune in RUNE_IDS for rune, _ in spell.runes)
    for e in JEWELLERY_ENCHANTS + BOLT_ENCHANTS:
        assert e.spell in SPELLS and e.input_id != e.output_id
    assert ELEMENTAL_RUNES <= set(RUNE_IDS)


def test_known_spells_match_the_game():
    high = SPELLS["high_alch"]
    assert (high.level, high.exp, high.ticks) == (55, 65, 5)
    assert dict(high.runes) == {"Nature": 1, "Fire": 5}
    assert items_per_hour(high) == 1200
    assert SPELLS["bolts_ruby"].per_cast == 10


# --- runes ---------------------------------------------------------------------

def test_rune_cost_with_and_without_a_staff():
    cost = rune_cost(SPELLS["high_alch"], runes(100, 110))
    assert cost.patient == 6 * 100            # nature + 5 fire
    assert cost.patient_staff == 100          # fire is free
    assert cost.instant == 6 * 110
    assert cost.instant_staff == 110


def test_bolt_runes_are_shared_across_ten_bolts_rounding_up():
    cost = rune_cost(SPELLS["bolts_ruby"], runes(101, 101))   # cosmic, 5 fire, blood
    assert cost.patient == -(-7 * 101 // 10)
    assert cost.patient_staff == -(-2 * 101 // 10)


def test_a_missing_rune_price_blanks_the_cost():
    prices = runes()
    prices[RUNE_IDS["Nature"]] = flip(RUNE_IDS["Nature"], None, None)
    assert rune_cost(SPELLS["high_alch"], prices).patient is None


# --- alchemy -------------------------------------------------------------------

def test_high_alch_profit_pays_no_tax_and_counts_runes():
    rows = build_magic_opportunities(by_id(flip(1, 1000, 1050, highalch=1500)), FRACTION)
    r = only(rows, HIGH_ALCH, 1)
    assert r.proceeds == 1500                       # coins, not a GE sale
    assert r.profit_staff == 1500 - 1000 - 100
    assert r.profit == 1500 - 1000 - 600
    assert r.instant_profit_staff == 1500 - 1050 - 110
    assert r.output_name == "Coins" and r.output_id is None


def test_alchemy_lists_only_items_that_could_pay():
    rows = build_magic_opportunities(by_id(
        flip(1, 1000, 1050, highalch=1101),   # 1 gp after a staffed nature rune
        flip(2, 1000, 1050, highalch=1100),   # breaks even: left out
        flip(3, None, None, highalch=9999),   # no price: left out
    ), FRACTION)
    assert [r.input_id for r in rows if r.kind == HIGH_ALCH] == [1]


def test_low_alch_uses_the_low_value():
    rows = build_magic_opportunities(by_id(flip(1, 100, 110, lowalch=400, highalch=600)), FRACTION)
    assert only(rows, LOW_ALCH, 1).proceeds == 400
    assert only(rows, HIGH_ALCH, 1).proceeds == 600


def test_alch_units_are_capped_by_casting_speed():
    rows = build_magic_opportunities(by_id(flip(1, 100, 110, highalch=1000, limit=None)), FRACTION)
    r = only(rows, HIGH_ALCH, 1)
    assert r.est_units_per_4h == 4 * 1200
    assert r.bottleneck == "casting speed"
    assert r.est_hours_casting == 4


def test_alch_units_are_capped_by_the_buy_limit():
    rows = build_magic_opportunities(by_id(flip(1, 100, 110, highalch=1000, limit=70)), FRACTION)
    r = only(rows, HIGH_ALCH, 1)
    assert (r.est_units_per_4h, r.bottleneck) == (70, "buy limit")
    assert r.est_profit_per_4h_staff == 70 * r.profit_staff
    assert r.capital_required == 70 * 100


# --- enchanting ----------------------------------------------------------------

def test_jewellery_enchant_sells_the_output_after_tax():
    e = JEWELLERY_ENCHANTS[0]
    rows = build_magic_opportunities(by_id(
        flip(e.input_id, 1000, 1100), flip(e.output_id, 1500, 1800),
    ), FRACTION)
    r = only(rows, JEWELLERY, e.input_id)
    assert r.proceeds == 1800 - ge_tax(1800)
    assert r.instant_proceeds == 1500 - ge_tax(1500)
    spell = SPELLS[e.spell]
    paid_runes = sum(n for rune, n in spell.runes if rune not in ELEMENTAL_RUNES)
    assert r.profit_staff == 1800 - ge_tax(1800) - 1000 - 100 * paid_runes
    assert r.level == spell.level


def test_enchant_volume_counts_the_output_sell_side():
    e = JEWELLERY_ENCHANTS[0]
    rows = build_magic_opportunities(by_id(
        flip(e.input_id, 1000, 1100, limit=None),
        flip(e.output_id, 1500, 1800, sell_vol=50, name="Thin output"),
    ), FRACTION)
    r = only(rows, JEWELLERY, e.input_id)
    assert r.est_units_per_4h == int(50 * 4 * FRACTION)
    assert r.bottleneck == "Thin output"


def test_every_enchant_is_listed_even_at_a_loss():
    e = BOLT_ENCHANTS[0]
    rows = build_magic_opportunities(by_id(
        flip(e.input_id, 1000, 1100), flip(e.output_id, 10, 12),
    ), FRACTION)
    r = only(rows, BOLTS, e.input_id)
    assert r.profit_staff < 0
    assert r.exp_per_item == SPELLS[e.spell].exp / 10


def test_members_flag_comes_from_spell_or_items():
    e = JEWELLERY_ENCHANTS[0]
    assert not SPELLS[e.spell].members
    rows = build_magic_opportunities(by_id(
        flip(e.input_id, 1000, 1100), flip(e.output_id, 1500, 1800, members=True),
    ), FRACTION)
    assert only(rows, JEWELLERY, e.input_id).members


def test_rows_rank_by_staffed_profit_per_4h():
    rows = build_magic_opportunities(by_id(
        flip(1, 100, 110, highalch=300, limit=10),
        flip(2, 100, 110, highalch=300, limit=1000),
    ), FRACTION)
    alchs = [r.input_id for r in rows if r.kind == HIGH_ALCH]
    assert alchs == [2, 1]
