"""Unit tests for the flip maths."""

from __future__ import annotations

import pytest

from flipfinder.flips import (
    GE_TAX_CAP_PER_ITEM,
    GE_TAX_FREE_BELOW,
    LONG_TERM_MIN_DRAWDOWN,
    SAFETY_ORDER,
    TREND_DANGEROUS_SLOPE,
    TREND_VOLATILE_STDEV,
    analyse_trend,
    assess_long_term,
    break_even_sell_price,
    build_all_flips,
    build_flip,
    estimate_fill_hours,
    expected_drift,
    estimated_units_per_4h,
    ge_tax,
    plan_flip,
    profit_per_hour,
    rank_by_profit,
    sell_price_for_profit,
    spread_sanity,
    suggest_prices,
    summarise_trade,
    value_position,
)


# --- tax ---------------------------------------------------------------------

def test_tax_is_two_percent_rounded_down():
    assert ge_tax(100) == 2
    assert ge_tax(1_000) == 20
    assert ge_tax(149) == 2        # 2.98 truncates to 2
    assert ge_tax(99) == 1         # 1.98 truncates to 1


def test_sub_50gp_items_are_exempt():
    assert ge_tax(49) == 0
    assert ge_tax(1) == 0
    assert ge_tax(0) == 0
    # The threshold itself is taxed, though 2% of 50 truncates to 1.
    assert GE_TAX_FREE_BELOW == 50
    assert ge_tax(50) == 1


def test_tax_is_capped_at_five_million():
    # The cap bites at a 250m sell price.
    assert ge_tax(250_000_000) == GE_TAX_CAP_PER_ITEM
    assert ge_tax(500_000_000) == GE_TAX_CAP_PER_ITEM
    assert ge_tax(2_000_000_000) == GE_TAX_CAP_PER_ITEM
    # Just below the cap we still pay the percentage.
    assert ge_tax(200_000_000) == 4_000_000


# --- units per 4h ------------------------------------------------------------

def test_units_use_the_thinner_side_of_the_book():
    # 100/hour is the limiting side: 100 * 4 * 0.10 = 40 units.
    assert estimated_units_per_4h(10_000, 100, 5_000, 0.10) == 40


def test_units_are_capped_by_the_buy_limit():
    # Volume would allow 4,000 but the limit is 70.
    assert estimated_units_per_4h(70, 10_000, 10_000, 0.10) == 70


def test_units_fall_back_to_volume_when_limit_unknown():
    # About 500 mapping entries have no "limit" field.
    assert estimated_units_per_4h(None, 250, 250, 0.10) == 100


def test_volume_fraction_scales_units():
    assert estimated_units_per_4h(10_000, 1_000, 1_000, 1.0) == 4_000
    assert estimated_units_per_4h(10_000, 1_000, 1_000, 0.25) == 1_000


def test_no_volume_means_no_units():
    assert estimated_units_per_4h(1_000, 0, 5_000, 0.10) == 0


# --- spread sanity check -----------------------------------------------------

def test_normal_spread_is_not_suspicious():
    # Live spread 100, hourly average 90: a ratio of 1.1.
    _, ratio, suspicious = spread_sanity(1_000, 1_100, 1_000, 1_090)
    assert pytest.approx(ratio, abs=0.01) == 1.11
    assert suspicious is False


def test_spread_far_wider_than_hourly_average_is_flagged():
    # Live spread 500 against an hourly average of 50: 10x.
    hour_spread, ratio, suspicious = spread_sanity(1_000, 1_500, 1_000, 1_050)
    assert hour_spread == 50
    assert ratio == 10.0
    assert suspicious is True


def test_spread_check_is_silent_without_hourly_data():
    # Untraded items have null averages; we cannot judge, so we do not flag.
    assert spread_sanity(1_000, 5_000, None, None) == (None, None, False)


def test_profit_from_a_flat_hourly_average_is_flagged():
    # No hourly spread at all, yet the live book claims one.
    _, _, suspicious = spread_sanity(1_000, 1_400, 1_200, 1_200)
    assert suspicious is True


# --- one item end to end -----------------------------------------------------

def make_item(**overrides):
    item = {
        "id": 4151,
        "name": "Abyssal whip",
        "icon": "Abyssal whip.png",
        "members": True,
        "examine": "A weapon from the abyss.",
        "lowalch": 48_000,
        "highalch": 72_000,
        "value": 120_001,
        "limit": 70,
    }
    item.update(overrides)
    return item


def test_build_flip_computes_the_whole_chain():
    item = make_item()
    latest = {"high": 1_000_000, "highTime": 1_000, "low": 900_000, "lowTime": 990}
    hourly = {
        "avgHighPrice": 995_000,
        "avgLowPrice": 905_000,
        "highPriceVolume": 300,
        "lowPriceVolume": 200,
    }

    flip = build_flip(item, latest, hourly, now=1_060, volume_fraction=0.10)

    assert flip.tax == 20_000                      # 2% of the 1m sell price
    assert flip.margin == 1_000_000 - 20_000 - 900_000  # 80,000
    assert flip.roi == pytest.approx(80_000 / 900_000)
    # Thinner side is 200/hour: 200 * 4 * 0.10 = 80, capped by the 70 limit.
    assert flip.est_units_per_4h == 70
    assert flip.est_profit_per_4h == 80_000 * 70
    assert flip.capital_required == 900_000 * 70
    # Age comes from the staler timestamp (lowTime 990, now 1060).
    assert flip.price_age_seconds == 70


def test_margin_can_be_negative_once_tax_is_taken():
    item = make_item(limit=100)
    latest = {"high": 1_000, "highTime": 10, "low": 990, "lowTime": 10}
    hourly = {
        "avgHighPrice": 1_000,
        "avgLowPrice": 990,
        "highPriceVolume": 500,
        "lowPriceVolume": 500,
    }
    flip = build_flip(item, latest, hourly, now=10, volume_fraction=0.10)
    # 1000 - 20 tax - 990 buy = -10 per unit.
    assert flip.margin == -10
    assert flip.est_profit_per_4h < 0


def test_missing_prices_leave_derived_fields_none():
    flip = build_flip(make_item(), {}, {}, now=100, volume_fraction=0.10)
    assert flip.low is None and flip.high is None
    assert flip.margin is None
    assert flip.roi is None
    assert flip.est_profit_per_4h is None
    assert flip.capital_required is None
    assert flip.price_age_seconds is None
    # Still present for the search box.
    assert flip.name == "Abyssal whip"


def test_sub_50gp_item_keeps_its_whole_spread():
    item = make_item(id=1, name="Bucket", limit=13_000, members=False)
    latest = {"high": 40, "highTime": 0, "low": 30, "lowTime": 0}
    hourly = {
        "avgHighPrice": 40,
        "avgLowPrice": 30,
        "highPriceVolume": 10_000,
        "lowPriceVolume": 10_000,
    }
    flip = build_flip(item, latest, hourly, now=0, volume_fraction=0.10)
    assert flip.tax == 0
    assert flip.margin == 10  # the full spread, untaxed


# --- ranking -----------------------------------------------------------------

def test_ranking_puts_the_best_estimated_profit_first():
    mapping = [
        make_item(id=1, name="Small", limit=1_000),
        make_item(id=2, name="Big", limit=1_000),
        make_item(id=3, name="Untraded", limit=1_000),
    ]
    latest = {
        "1": {"high": 1_100, "highTime": 0, "low": 1_000, "lowTime": 0},
        "2": {"high": 11_000, "highTime": 0, "low": 10_000, "lowTime": 0},
        # id 3 absent: no price data at all.
    }
    hourly = {
        "1": {"avgHighPrice": 1_100, "avgLowPrice": 1_000,
              "highPriceVolume": 1_000, "lowPriceVolume": 1_000},
        "2": {"avgHighPrice": 11_000, "avgLowPrice": 10_000,
              "highPriceVolume": 1_000, "lowPriceVolume": 1_000},
    }

    ranked = build_all_flips(mapping, latest, hourly, now=0, volume_fraction=0.10)

    assert [f.name for f in ranked] == ["Big", "Small", "Untraded"]
    # The item with no prices sorts last rather than being dropped.
    assert ranked[-1].est_profit_per_4h is None


def test_ranking_is_stable_for_equal_profits():
    mapping = [make_item(id=i, name=f"Item {i}") for i in (1, 2)]
    latest = {
        "1": {"high": 1_100, "highTime": 0, "low": 1_000, "lowTime": 0},
        "2": {"high": 1_100, "highTime": 0, "low": 1_000, "lowTime": 0},
    }
    hourly = {
        str(i): {"avgHighPrice": 1_100, "avgLowPrice": 1_000,
                 "highPriceVolume": 500, "lowPriceVolume": 500}
        for i in (1, 2)
    }
    ranked = build_all_flips(mapping, latest, hourly, now=0, volume_fraction=0.10)
    assert ranked[0].est_profit_per_4h == ranked[1].est_profit_per_4h


def test_rank_by_profit_handles_an_all_none_list():
    flips = [build_flip(make_item(id=i), {}, {}, now=0, volume_fraction=0.1) for i in (1, 2)]
    assert len(rank_by_profit(flips)) == 2


# --- suggested prices --------------------------------------------------------

def test_suggestion_moves_both_offers_inward_by_the_same_step():
    # Passive margin here is 11000 - 220 tax - 10000 = 780. At 25% we are
    # willing to spend 195 of it, so 97 a side.
    s = suggest_prices(low=10_000, high=11_000, aggression=0.25)
    assert s is not None
    assert s.step == 97
    assert s.buy == 10_097
    assert s.sell == 10_903
    # Net is the arithmetic the caller can rely on.
    assert s.net_per_unit == 10_903 - ge_tax(10_903) - 10_097
    # Never more than the share asked for.
    assert s.margin_conceded <= 0.25


def test_suggestion_is_never_worse_than_the_profit_floor():
    # A 20gp spread on a 1000gp item: tax alone is 20, so the passive margin is
    # zero and nothing can be suggested.
    assert suggest_prices(low=1_000, high=1_020, aggression=0.25) is None


def test_suggestion_backs_off_when_the_floor_would_be_broken():
    # Passive margin is 10. Asking for all of it would net zero, under the
    # 1gp floor, so it must settle for less than the full request.
    s = suggest_prices(low=1_000, high=1_030, aggression=1.0, min_profit_per_unit=1)
    assert s is not None
    assert s.net_per_unit >= 1
    assert s.margin_conceded < 1.0, "should have backed off from the request"
    # One gp more aggressive would break the floor.
    harder = s.sell - 1 - ge_tax(s.sell - 1) - (s.buy + 1)
    assert harder < 1


def test_zero_aggression_is_the_passive_price_pair():
    s = suggest_prices(low=5_000, high=5_500, aggression=0.0)
    assert s is not None
    assert (s.buy, s.sell, s.step) == (5_000, 5_500, 0)


def test_suggestion_respects_a_higher_profit_floor():
    loose = suggest_prices(low=1_000, high=1_200, aggression=0.9, min_profit_per_unit=1)
    strict = suggest_prices(low=1_000, high=1_200, aggression=0.9, min_profit_per_unit=100)
    assert loose is not None and strict is not None
    assert strict.net_per_unit >= 100
    # A stricter floor forces a more passive, wider-margin pair.
    assert strict.step < loose.step


def test_suggestion_needs_a_real_spread():
    assert suggest_prices(low=None, high=100) is None
    assert suggest_prices(low=100, high=None) is None
    assert suggest_prices(low=100, high=100) is None   # no spread
    assert suggest_prices(low=200, high=100) is None   # inverted book


def test_sub_50gp_suggestion_pays_no_tax():
    s = suggest_prices(low=30, high=48, aggression=0.5, min_profit_per_unit=1)
    assert s is not None
    # Both suggested prices stay under the threshold, so the whole step is kept.
    assert s.sell < GE_TAX_FREE_BELOW
    assert s.net_per_unit == s.sell - s.buy


# --- time to fill ------------------------------------------------------------

def test_fill_hours_use_the_opposite_volume_to_the_name():
    # Your BUY is filled by instant-sellers (hour_low_volume) and your SELL by
    # instant-buyers (hour_high_volume).
    buy_h, sell_h, total = estimate_fill_hours(
        units=100, hour_low_volume=1_000, hour_high_volume=500, volume_fraction=0.10
    )
    assert buy_h == pytest.approx(100 / 100)   # 1000 * 0.10 = 100/hour
    assert sell_h == pytest.approx(100 / 50)   # 500 * 0.10 = 50/hour
    assert total == pytest.approx(buy_h + sell_h)


def test_fill_hours_are_none_without_units_or_volume():
    assert estimate_fill_hours(0, 100, 100, 0.1) == (None, None, None)
    assert estimate_fill_hours(None, 100, 100, 0.1) == (None, None, None)
    assert estimate_fill_hours(100, 0, 100, 0.1) == (None, None, None)
    assert estimate_fill_hours(100, 100, 0, 0.1) == (None, None, None)


def test_round_trip_is_about_eight_hours_at_full_size():
    """A sanity check on how est_units_per_4h is sized.

    Units are set so the thinner side alone consumes the full 4h window, which
    means the round trip takes roughly twice that. This is why a hold-time
    filter is worth having.
    """
    units = estimated_units_per_4h(
        buy_limit=10 ** 9, hour_low_volume=1_000, hour_high_volume=1_000,
        volume_fraction=0.10,
    )
    _, _, total = estimate_fill_hours(units, 1_000, 1_000, 0.10)
    assert total == pytest.approx(8.0)


# --- suggestions on a whole item --------------------------------------------

def test_build_flip_carries_the_suggestion_and_hours():
    item = make_item(limit=10_000)
    latest = {"high": 11_000, "highTime": 0, "low": 10_000, "lowTime": 0}
    hourly = {
        "avgHighPrice": 11_000, "avgLowPrice": 10_000,
        "highPriceVolume": 1_000, "lowPriceVolume": 1_000,
    }
    flip = build_flip(item, latest, hourly, now=0, volume_fraction=0.10, aggression=0.25)

    assert flip.suggested_buy == 10_097
    assert flip.suggested_sell == 10_903
    # The suggested prices are strictly inside the raw book.
    assert flip.low < flip.suggested_buy < flip.suggested_sell < flip.high
    # Faster fill costs margin.
    assert flip.suggested_net_per_unit < flip.margin
    assert flip.suggested_profit_per_4h < flip.est_profit_per_4h
    assert flip.est_hours_total == pytest.approx(8.0)


def test_item_with_no_viable_suggestion_still_builds():
    item = make_item()
    latest = {"high": 1_010, "highTime": 0, "low": 1_000, "lowTime": 0}
    hourly = {"avgHighPrice": 1_010, "avgLowPrice": 1_000,
              "highPriceVolume": 500, "lowPriceVolume": 500}
    flip = build_flip(item, latest, hourly, now=0, volume_fraction=0.10)
    # Tax of 20 exceeds the 10gp spread, so there is nothing to suggest.
    assert flip.margin < 0
    assert flip.suggested_buy is None
    assert flip.suggested_profit_per_4h is None
    # The rest of the row is still populated for the search box.
    assert flip.name == "Abyssal whip"


# --- the trade planner -------------------------------------------------------

def plan(**overrides):
    """plan_flip with a sensible liquid-market default, overridable per test."""
    kwargs = dict(
        buy=1_000, sell=1_100, quantity=100,
        low=1_000, high=1_100,
        hour_low_volume=10_000, hour_high_volume=10_000,
        buy_limit=10_000, volume_fraction=0.10,
    )
    kwargs.update(overrides)
    return plan_flip(**kwargs)


def test_plan_totals_are_per_unit_times_quantity():
    p = plan(buy=1_000, sell=1_100, quantity=250)
    assert p.tax_per_unit == 22                  # 2% of 1100
    assert p.net_per_unit == 1_100 - 22 - 1_000  # 78
    assert p.total_net == 78 * 250
    assert p.total_tax == 22 * 250
    assert p.total_capital == 1_000 * 250
    assert p.roi == pytest.approx(78 / 1_000)


def test_plan_warns_when_the_trade_loses_money():
    p = plan(buy=1_090, sell=1_100)
    assert p.net_per_unit == -12                 # 1100 - 22 tax - 1090
    assert p.total_net < 0
    assert any("Loses" in w for w in p.warnings)


def test_plan_flags_breaking_even():
    # 1100 sell, 22 tax, so a 1078 buy nets exactly zero.
    p = plan(buy=1_078, sell=1_100)
    assert p.net_per_unit == 0
    assert any("even" in w for w in p.warnings)


def test_sub_50gp_plan_pays_no_tax():
    p = plan(buy=30, sell=45, quantity=1_000, low=30, high=45)
    assert p.tax_per_unit == 0
    assert p.net_per_unit == 15
    assert p.total_net == 15_000


def test_crossing_the_spread_fills_faster_than_sitting_passive():
    passive = plan(buy=1_000, sell=1_100)   # both at the passive end
    crossed = plan(buy=1_100, sell=1_000)   # both crossing entirely
    assert passive.est_hours_total > crossed.est_hours_total
    assert crossed.est_hours_total == pytest.approx(0.0)
    # Crossing costs money, which is the whole trade-off.
    assert crossed.net_per_unit < passive.net_per_unit


def test_halfway_prices_roughly_halve_the_wait():
    passive = plan(buy=1_000, sell=1_100)
    halfway = plan(buy=1_050, sell=1_050)
    assert halfway.est_hours_total == pytest.approx(passive.est_hours_total / 2)


def test_speed_bands_follow_the_round_trip_estimate():
    # Thin market, large order: slow. 1000 units against 100/hour a side.
    slow = plan(quantity=1_000, hour_low_volume=1_000, hour_high_volume=1_000,
                buy_limit=10_000)
    assert slow.est_hours_total == pytest.approx(20.0)
    assert slow.speed == "very slow"

    quick = plan(quantity=10, hour_low_volume=10_000, hour_high_volume=10_000)
    assert quick.speed == "fast"


def test_speed_is_unknown_without_volume():
    p = plan(hour_low_volume=0, hour_high_volume=5_000)
    assert p.est_hours_total is None
    assert p.speed == "unknown"
    assert any("may not fill" in w for w in p.warnings)


def test_plan_warns_about_bidding_outside_the_book():
    under = plan(buy=900, sell=1_100)
    assert any("below the current best bid" in w for w in under.warnings)
    over = plan(buy=1_000, sell=1_200)
    assert any("above the current best ask" in w for w in over.warnings)



def test_plan_warns_when_quantity_exceeds_the_buy_limit():
    p = plan(quantity=5_000, buy_limit=1_000)
    assert any("buy limit" in w for w in p.warnings)
    # The arithmetic still runs, it is the player's call.
    assert p.total_net == p.net_per_unit * 5_000


def test_plan_passes_through_the_spread_and_staleness_warnings():
    p = plan(suspicious_spread=True, price_age_seconds=1_800)
    assert any("spike" in w for w in p.warnings)
    assert any("30 minutes old" in w for w in p.warnings)


def test_zero_quantity_is_handled_without_dividing_by_zero():
    p = plan(quantity=0)
    assert p.total_net == 0
    assert p.total_capital == 0
    assert p.est_hours_total is None
    assert any("Quantity is zero" in w for w in p.warnings)


def test_negative_quantity_is_clamped_to_zero():
    p = plan(quantity=-50)
    assert p.quantity == 0
    assert p.total_net == 0


def test_prices_outside_the_book_get_no_speed_estimate():
    """A passive price outside the book only fills if the market moves to it.

    Volume cannot tell us whether it will, so the planner must decline to
    guess rather than show a cheerful estimate next to its own warning.
    """
    under = plan(buy=900, sell=1_100)
    assert under.est_hours_total is None
    assert under.speed == "unknown"
    assert any("only fills if the price falls" in w for w in under.warnings)

    over = plan(buy=1_000, sell=1_200)
    assert over.est_hours_total is None
    assert over.speed == "unknown"
    assert any("only fills if the price rises" in w for w in over.warnings)

    # The money side is still exact; only the timing is unknowable.
    assert under.net_per_unit == 1_100 - ge_tax(1_100) - 900


def test_prices_inside_the_book_still_get_an_estimate():
    inside = plan(buy=1_010, sell=1_090)
    assert inside.est_hours_total is not None
    assert inside.speed != "unknown"


def test_a_zero_buy_price_reports_only_that_problem():
    """An empty detail view should not also claim the trade breaks even."""
    p = plan(buy=0, sell=0, quantity=1, low=None, high=None,
             hour_low_volume=0, hour_high_volume=0)
    assert any("above zero" in w for w in p.warnings)
    assert not any("even" in w for w in p.warnings)
    assert p.roi is None


# --- trend and safety --------------------------------------------------------

def series(prices):
    """Build timeseries points from a list of mid prices."""
    return [
        {"timestamp": i * 86_400, "avgHighPrice": p + 1, "avgLowPrice": p - 1,
         "highPriceVolume": 100, "lowPriceVolume": 100}
        for i, p in enumerate(prices)
    ]


def test_flat_price_is_safe_and_flat():
    t = analyse_trend(series([1_000] * 60), timestep="24h")
    assert t is not None
    assert t.direction == "flat"
    assert t.safety == "safe"
    assert t.slope_per_day == pytest.approx(0.0, abs=1e-9)
    assert t.change_30d == pytest.approx(0.0)


def test_steadily_rising_price_is_safe_and_rising():
    # 1% a day compounding is comfortably a rise.
    t = analyse_trend(series([1_000 * (1.01 ** i) for i in range(60)]), timestep="24h")
    assert t.direction == "rising"
    assert t.safety == "safe"
    assert t.slope_per_day > 0
    assert t.change_7d > 0


def test_steadily_falling_price_is_rated_dangerous():
    # Losing 1% a day for two months is exactly what the rating is for.
    t = analyse_trend(series([1_000 * (0.99 ** i) for i in range(60)]), timestep="24h")
    assert t.direction == "falling"
    assert t.safety == "dangerous"
    assert t.slope_per_day <= TREND_DANGEROUS_SLOPE
    assert t.change_30d < 0


def test_a_gentle_decline_is_not_treated_as_dangerous():
    """About 0.1% a day is real, but nothing like a collapse."""
    t = analyse_trend(series([1_000 * (0.999 ** i) for i in range(90)]), timestep="24h")
    assert t.direction == "falling"
    assert t.safety not in ("safe", "dangerous")


def test_slope_is_reported_per_day_whatever_the_timestep():
    """The same real-world decline must read the same at any resolution."""
    daily = analyse_trend(series([1_000 - 5 * i for i in range(40)]), timestep="24h")
    # Same 5gp a day, sampled every 6 hours instead.
    six_hourly = analyse_trend(
        series([1_000 - 5 * (i / 4) for i in range(160)]), timestep="6h"
    )
    assert daily.slope_per_day == pytest.approx(six_hourly.slope_per_day, rel=0.02)


def test_volatility_downgrades_an_otherwise_flat_price():
    # Flat on average but swinging 20% every day.
    jumpy = [1_000 * (1.2 if i % 2 else 0.8) for i in range(60)]
    t = analyse_trend(series(jumpy), timestep="24h")
    assert t.volatility_daily > TREND_VOLATILE_STDEV
    assert t.safety != "safe"
    assert any("Jumpy" in n for n in t.notes)


def test_catching_a_falling_knife_rates_worse_than_the_same_slope_off_the_floor():
    """Two identical declines, rated differently by where they end up.

    Both lose about 0.1% a day. The smooth one finishes at its lowest price of
    the whole window, which is the classic trap: still falling, nothing under
    it. The sawtooth one has bounced off its floor, so it keeps the milder
    rating despite the same trend.
    """
    on_the_floor = analyse_trend(
        series([1_000 * (0.999 ** i) for i in range(90)]), timestep="24h"
    )
    off_the_floor = analyse_trend(
        series([1_000 * (0.999 ** i) + (25 if i % 2 else 0) for i in range(90)]),
        timestep="24h",
    )

    assert on_the_floor.slope_per_day == pytest.approx(
        off_the_floor.slope_per_day, rel=0.05
    ), "the two series must share a trend for the comparison to mean anything"

    assert on_the_floor.position_in_range < 0.15
    assert off_the_floor.position_in_range > 0.15

    assert SAFETY_ORDER.index(on_the_floor.safety) < SAFETY_ORDER.index(
        off_the_floor.safety
    ), "sitting on the floor while falling should rate worse"
    assert any("lowest price" in n for n in on_the_floor.notes)
    assert not any("lowest price" in n for n in off_the_floor.notes)


def test_drawdown_and_position_describe_where_we_sit():
    # Rises to 2000 then falls back to 1000.
    prices = list(range(1_000, 2_001, 50)) + list(range(1_950, 999, -50))
    t = analyse_trend(series(prices), timestep="24h")
    assert t.drawdown_from_peak == pytest.approx(0.5, abs=0.01)
    assert t.position_in_range == pytest.approx(0.0, abs=0.01)


def test_too_little_history_returns_nothing():
    assert analyse_trend(series([1_000, 1_010]), timestep="24h") is None
    assert analyse_trend([], timestep="24h") is None


def test_points_with_one_missing_side_still_count():
    points = series([1_000] * 30)
    for p in points[:10]:
        p["avgLowPrice"] = None        # only an instant-buy price that period
    t = analyse_trend(points, timestep="24h")
    assert t is not None
    assert t.points_used == 30


def test_points_with_no_prices_at_all_are_skipped():
    points = series([1_000] * 30)
    for p in points[:10]:
        p["avgHighPrice"] = None
        p["avgLowPrice"] = None
    t = analyse_trend(points, timestep="24h")
    assert t.points_used == 20


def test_unknown_timestep_is_rejected():
    with pytest.raises(ValueError):
        analyse_trend(series([1_000] * 30), timestep="3h")


# --- drift against the margin ------------------------------------------------

def test_expected_drift_scales_with_the_hold():
    falling = analyse_trend(series([1_000 * (0.99 ** i) for i in range(60)]), timestep="24h")
    # Over a full day the drift should be close to the daily slope.
    day = expected_drift(falling, price=1_000, hold_hours=24)
    assert day == pytest.approx(falling.slope_per_day * 1_000, rel=1e-6)
    # Half the hold, half the drift.
    half = expected_drift(falling, price=1_000, hold_hours=12)
    assert half == pytest.approx(day / 2, rel=1e-6)
    assert day < 0, "a falling trend should drift the price down"


def test_expected_drift_needs_a_trend_and_a_hold():
    t = analyse_trend(series([1_000] * 30), timestep="24h")
    assert expected_drift(None, 1_000, 4) is None
    assert expected_drift(t, 1_000, None) is None
    assert expected_drift(t, 0, 4) is None


def test_change_notes_read_as_plain_english():
    """No double negatives, and a flat week should not read as a rise."""
    falling = analyse_trend(series([1_000 * (0.99 ** i) for i in range(60)]), timestep="24h")
    assert any(n.startswith("Down ") for n in falling.notes)
    assert not any("Down -" in n for n in falling.notes)
    assert not any("Up +" in n for n in falling.notes)

    flat = analyse_trend(series([1_000] * 60), timestep="24h")
    assert any("Flat over 30 days." == n for n in flat.notes)
    assert not any(n.startswith("Up ") for n in flat.notes)

    rising = analyse_trend(series([1_000 * (1.01 ** i) for i in range(60)]), timestep="24h")
    assert any(n.startswith("Up ") for n in rising.notes)


# --- break-even and open positions -------------------------------------------

def test_break_even_covers_the_tax_on_the_sale():
    # Selling at 1000 pays 20 tax, so 1000 does not cover a 1000 buy. Tax on
    # 1020 truncates to 20 as well, so 1020 is the first price that clears.
    assert break_even_sell_price(1_000) == 1_020
    assert 1_020 - ge_tax(1_020) - 1_000 == 0
    assert 1_019 - ge_tax(1_019) - 1_000 < 0, "1019 must not be enough"


def test_break_even_is_the_lowest_price_that_works():
    """The defining property, checked across a wide range of buy prices."""
    for buy in (1, 49, 50, 51, 99, 1_000, 12_345, 1_000_000, 249_999_999):
        price = break_even_sell_price(buy)
        assert price - ge_tax(price) - buy >= 0, f"{price} should cover {buy}"
        if price > 0:
            assert (price - 1) - ge_tax(price - 1) - buy < 0, (
                f"{price - 1} should not cover {buy}, so {price} is not minimal"
            )


def test_cheap_items_break_even_at_what_they_cost():
    # Under the exemption there is no tax, so the buy price is the break-even.
    assert break_even_sell_price(30) == 30
    assert break_even_sell_price(49) == 49


def test_break_even_above_the_tax_cap_adds_a_flat_five_million():
    # Past 250m the tax stops growing, so you need exactly the cap back.
    buy = 400_000_000
    assert break_even_sell_price(buy) == buy + GE_TAX_CAP_PER_ITEM


def test_target_price_for_a_chosen_profit():
    price = sell_price_for_profit(1_000, profit_per_unit=100)
    assert price - ge_tax(price) - 1_000 >= 100
    assert (price - 1) - ge_tax(price - 1) - 1_000 < 100
    # A bigger target always needs a higher price.
    assert sell_price_for_profit(1_000, 500) > price


def test_target_price_rejects_a_negative_buy():
    with pytest.raises(ValueError):
        sell_price_for_profit(-1)


def test_position_in_profit_reports_what_a_sale_would_make():
    # Bought 100 at 1000, the book now pays 1100.
    p = value_position(buy_price=1_000, quantity=100, current_high=1_100)
    assert p.break_even == 1_020
    assert p.in_profit is True
    assert p.net_per_unit_now == 1_100 - ge_tax(1_100) - 1_000   # 78
    assert p.total_now == 78 * 100
    assert p.outlay == 100_000
    assert p.gap_to_break_even == 0


def test_position_underwater_reports_the_gap_to_break_even():
    # Bought 50 at 1000, the price has slid to 950.
    p = value_position(buy_price=1_000, quantity=50, current_high=950)
    assert p.in_profit is False
    assert p.net_per_unit_now < 0
    assert p.total_now == p.net_per_unit_now * 50
    # The price has to climb back to the break-even to get out level.
    assert p.gap_to_break_even == p.break_even - 950


def test_position_with_no_live_price_still_reports_break_even():
    p = value_position(buy_price=1_000, quantity=10, current_high=None)
    assert p.break_even == 1_020
    assert p.sell_now is None
    assert p.total_now is None
    assert p.in_profit is None
    assert p.outlay == 10_000


# --- live trades with sell lots ----------------------------------------------

def lot(lot_id, quantity, price, sold=False):
    return {"lot_id": lot_id, "quantity": quantity, "price": price, "sold": sold}


def trade(**overrides):
    """A half-filled trade in a liquid market, overridable per test."""
    kwargs = dict(
        buy_price=100, buy_quantity=1_000, bought=600,
        lots=[], low=100, high=120, reference_sell=120,
    )
    kwargs.update(overrides)
    return summarise_trade(**kwargs)


def test_units_are_split_between_sold_listed_and_loose():
    t = trade(lots=[lot("a", 200, 118, sold=True), lot("b", 300, 125)])
    assert t.units_sold == 200
    assert t.units_listed == 300
    assert t.units_unlisted == 100, "600 bought, 500 accounted for"
    assert t.remaining_to_buy == 400, "1000 ordered, 600 filled"


def test_outlay_counts_what_has_actually_filled():
    # You have not paid for the 400 still on offer.
    t = trade(bought=600, buy_price=100)
    assert t.outlay == 60_000


def test_realised_profit_comes_only_from_sold_lots():
    t = trade(lots=[lot("a", 200, 120, sold=True), lot("b", 300, 130)])
    # Sold at 120: 120 - 2 tax - 100 buy = 18 a unit.
    assert t.realised_net == 18 * 200
    # The open lot is counted separately, not as money in hand.
    assert t.listed_net_if_filled == (130 - ge_tax(130) - 100) * 300


def test_projection_adds_banked_listed_and_loose_stock():
    t = trade(lots=[lot("a", 200, 120, sold=True), lot("b", 300, 130)])
    # 100 loose units valued at the current instant-buy price of 120.
    assert t.unlisted_value_now == (120 - ge_tax(120) - 100) * 100
    assert t.projected_total == (
        t.realised_net + t.listed_net_if_filled + t.unlisted_value_now
    )


def test_lot_outlook_reads_the_book():
    # low 100, high 120.
    t = trade(lots=[
        lot("cross", 10, 95),      # under the best bid
        lot("front", 10, 115),     # under the best ask
        lot("patient", 10, 150),   # asking more than anyone else
        lot("gone", 10, 118, sold=True),
    ])
    outlooks = {l.lot_id: l.outlook for l in t.lots}
    assert outlooks["cross"] == "immediate"
    assert outlooks["front"] == "competitive"
    assert outlooks["patient"] == "above market"
    assert outlooks["gone"] == "filled"


def test_lot_outlook_is_unknown_without_prices():
    t = trade(low=None, high=None, lots=[lot("a", 10, 150)])
    assert t.lots[0].outlook == "unknown"


def test_each_lot_carries_its_own_profit():
    t = trade(buy_price=100, lots=[lot("cheap", 50, 110), lot("dear", 50, 200)])
    cheap, dear = t.lots
    assert cheap.net_per_unit == 110 - ge_tax(110) - 100
    assert dear.net_per_unit == 200 - ge_tax(200) - 100
    assert dear.total_net > cheap.total_net
    assert cheap.total_net == cheap.net_per_unit * 50


def test_a_lot_listed_below_break_even_shows_a_loss():
    t = trade(buy_price=100, lots=[lot("bad", 100, 101)])
    # 101 - 2 tax - 100 = -1 a unit.
    assert t.lots[0].net_per_unit == -1
    assert t.lots[0].total_net == -100


# --- advice on the rest of the buy offer -------------------------------------

def test_advice_says_keep_while_the_margin_holds():
    t = trade(high=120, reference_sell=120)
    assert t.buy_advice == "keep"
    assert t.current_net_per_unit == 120 - ge_tax(120) - 100   # 18


def test_advice_says_stop_once_the_market_pays_less_than_you_pay():
    # The price has slid to 100, so buying at 100 and selling nets a loss.
    t = trade(high=100, low=98)
    assert t.buy_advice == "stop"
    assert t.current_net_per_unit < 0
    assert "loses" in t.advice_reason


def test_advice_says_stop_at_exactly_break_even():
    # 102 sells for 102, pays 2 tax, leaving exactly the 100 it cost.
    t = trade(high=102, low=100)
    assert t.current_net_per_unit == 0
    assert t.buy_advice == "stop"
    assert "break exactly even" in t.advice_reason


def test_advice_warns_when_the_margin_has_thinned():
    # Opened when the book paid 150, now it pays only 110.
    t = trade(high=110, reference_sell=150)
    assert t.buy_advice == "thinning"
    assert t.current_net_per_unit > 0, "still profitable, just much less so"
    assert "fallen from" in t.advice_reason


def test_a_filled_buy_offer_needs_no_advice():
    t = trade(buy_quantity=600, bought=600, high=50)
    assert t.remaining_to_buy == 0
    assert t.buy_advice == "done"


def test_advice_is_unknown_without_a_live_price():
    t = trade(high=None, low=None)
    assert t.buy_advice == "unknown"


def test_thinning_needs_a_reference_to_compare_against():
    # With no snapshot from when the trade opened we cannot call it thinning,
    # only profitable or not.
    t = trade(high=110, reference_sell=None)
    assert t.buy_advice == "keep"
    assert t.reference_net_per_unit is None


# --- edges -------------------------------------------------------------------

def test_a_trade_with_nothing_bought_yet_is_still_coherent():
    t = trade(bought=0, lots=[])
    assert t.outlay == 0
    assert t.units_unlisted == 0
    assert t.realised_net == 0
    assert t.projected_total == 0
    assert t.remaining_to_buy == 1_000
    assert t.buy_advice == "keep"


def test_listing_more_than_you_hold_does_not_go_negative():
    # Guards against a typo in the lot quantity.
    t = trade(bought=100, lots=[lot("a", 500, 130)])
    assert t.units_unlisted == 0


def test_break_even_is_reported_for_the_trade():
    t = trade(buy_price=1_000)
    assert t.break_even == break_even_sell_price(1_000)


def test_aggression_is_measured_against_profit_not_the_raw_spread():
    """The spread is mostly tax on expensive items, so budgeting against it
    used to surrender the entire margin while reporting a small percentage.

    These are real Abyssal whip prices: an 18,381 gp spread hiding a margin of
    only 2,454. A quarter of the spread would be 4,595 a unit, nearly twice
    the whole profit.
    """
    low, high = 778_000, 796_381
    margin = high - ge_tax(high) - low
    assert margin == 2_454
    assert (high - low) > 7 * margin, "tax dominates the spread here"

    s = suggest_prices(low, high, aggression=0.25)
    assert s is not None
    assert s.margin_conceded <= 0.25, "must never exceed the share requested"
    # The old model left a single gold piece on a 778k item.
    assert s.net_per_unit > margin * 0.7
    assert s.net_per_unit == 1_848


def test_the_share_conceded_never_exceeds_the_setting():
    """Across a wide range of prices, including the tax cap and the exemption."""
    for low, high in [
        (30, 48), (45, 60), (100, 130), (1_000, 1_030), (12_345, 13_000),
        (250_000, 260_000), (778_000, 796_381), (300_000_000, 320_000_000),
    ]:
        for aggression in (0.0, 0.1, 0.25, 0.5):
            s = suggest_prices(low, high, aggression=aggression)
            if s is None:
                continue
            assert s.margin_conceded <= aggression + 1e-9, (
                f"{low}->{high} at {aggression} conceded {s.margin_conceded}"
            )
            assert s.net_per_unit >= 1
            assert low <= s.buy < s.sell <= high


# --- quick flips: profit per hour --------------------------------------------

def test_profit_per_hour_divides_by_the_round_trip():
    assert profit_per_hour(100_000, 4.0) == 25_000
    assert profit_per_hour(100_000, 0.5) == 200_000


def test_a_faster_flip_works_your_gold_harder():
    """Two flips earning the same, one taking a quarter of the time."""
    slow = profit_per_hour(50_000, 8.0)
    fast = profit_per_hour(50_000, 2.0)
    assert fast == 4 * slow


def test_profit_per_hour_is_none_without_a_time_estimate():
    assert profit_per_hour(100_000, None) is None
    assert profit_per_hour(100_000, 0) is None
    assert profit_per_hour(None, 4.0) is None


def test_build_flip_carries_profit_per_hour():
    item = make_item(limit=10_000)
    latest = {"high": 11_000, "highTime": 0, "low": 10_000, "lowTime": 0}
    hourly = {"avgHighPrice": 11_000, "avgLowPrice": 10_000,
              "highPriceVolume": 1_000, "lowPriceVolume": 1_000}
    flip = build_flip(item, latest, hourly, now=0, volume_fraction=0.10)
    assert flip.est_hours_total == pytest.approx(8.0)
    assert flip.est_profit_per_hour == int(flip.est_profit_per_4h / 8.0)


# --- long term: rising, but cheap today --------------------------------------

def rising_then_dipping():
    """Climbs steadily for 300 days, then pulls back hard at the end.

    Long-run trend is up, but today's price is near the bottom of the range,
    which is the shape the long-term view is looking for.
    """
    climb = [1_000 * (1.004 ** i) for i in range(300)]
    peak = climb[-1]
    dip = [peak * (1 - 0.004 * i) for i in range(1, 61)]
    return series(climb + dip)


def test_a_rising_price_in_a_dip_qualifies():
    t = analyse_trend(rising_then_dipping(), timestep="24h")
    view = assess_long_term(t)
    assert view is not None
    assert view.slope_per_day > 0, "the long-run trend is up"
    assert view.drawdown_from_peak >= LONG_TERM_MIN_DRAWDOWN, "and it has pulled back"
    assert view.qualifies
    assert view.score > 0
    assert "Rising" in view.reason


def test_a_falling_price_never_qualifies_however_cheap():
    t = analyse_trend(series([1_000 * (0.99 ** i) for i in range(120)]), timestep="24h")
    view = assess_long_term(t)
    assert view.drawdown_from_peak > 0.5, "it is certainly well off its peak"
    assert not view.qualifies, "but falling, so it is a knife not a dip"
    assert "flat or falling" in view.reason


def test_a_rising_price_at_its_peak_does_not_qualify():
    t = analyse_trend(series([1_000 * (1.01 ** i) for i in range(120)]), timestep="24h")
    view = assess_long_term(t)
    assert view.slope_per_day > 0
    assert view.drawdown_from_peak < LONG_TERM_MIN_DRAWDOWN, "it is at its high"
    assert not view.qualifies, "the rise has already happened"
    assert "already happened" in view.reason


def test_a_flat_price_does_not_qualify():
    t = analyse_trend(series([1_000] * 120), timestep="24h")
    view = assess_long_term(t)
    assert not view.qualifies


def test_a_deeper_dip_scores_higher_than_a_shallow_one():
    """Same climb, different pullbacks. The deeper one is the better entry."""
    climb = [1_000 * (1.004 ** i) for i in range(300)]
    peak = climb[-1]
    shallow = assess_long_term(
        analyse_trend(series(climb + [peak * 0.97, peak * 0.96]), timestep="24h")
    )
    deep = assess_long_term(
        analyse_trend(series(climb + [peak * (1 - 0.004 * i) for i in range(1, 61)]),
                      timestep="24h")
    )
    assert deep.drawdown_from_peak > shallow.drawdown_from_peak
    assert deep.score > shallow.score


def test_thresholds_can_be_loosened():
    t = analyse_trend(series([1_000 * (1.01 ** i) for i in range(120)]), timestep="24h")
    assert not assess_long_term(t).qualifies
    # Accepting any pullback at all lets a pure riser through.
    assert assess_long_term(t, min_drawdown=0.0).qualifies


def test_no_trend_data_means_no_verdict():
    assert assess_long_term(None) is None
