"""Flip maths.

Pure functions only, no network or clock access beyond what is passed in,
so this module is easy to test and easy to read.

Price semantics in the OSRS Wiki API:
    high = the instant-BUY price (what you pay to buy right now)
    low  = the instant-SELL price (what you receive to sell right now)
A flipper therefore buys near `low` and sells near `high`.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

# --- Grand Exchange tax rules ------------------------------------------------
# Update these three constants if Jagex changes the tax.

GE_TAX_RATE = 0.02            # 2% of the sell price
GE_TAX_CAP_PER_ITEM = 5_000_000  # never more than 5m per item
GE_TAX_FREE_BELOW = 50        # items selling under 50gp are untaxed


def ge_tax(sell_price: int) -> int:
    """Tax paid when selling one item at `sell_price`.

    Rounded down, as the game does. Items under the exemption threshold pay
    nothing, and the charge is capped per item.
    """
    if sell_price < GE_TAX_FREE_BELOW:
        return 0
    tax = int(sell_price * GE_TAX_RATE)  # int() truncates toward zero
    return min(tax, GE_TAX_CAP_PER_ITEM)


# --- Suggested prices --------------------------------------------------------
# Buying at `low` and selling at `high` earns the widest margin, but those are
# the slowest prices on the book: you join the back of a queue and wait for
# someone to trade into you. Nudging the buy up and the sell down jumps you
# ahead of that queue at the cost of margin.
#
# `aggression` is the fraction of your PROFIT you are willing to give up for a
# faster fill, split evenly across the two sides. 0.0 is fully passive, 1.0
# would hand over the whole margin.
#
# It is measured against the post-tax margin rather than the raw spread,
# because the margin is what you actually lose. On an expensive item the tax
# can be most of the spread: a whip at 778,000 to 796,381 has a spread of
# 18,381 but a margin of only 2,454. Conceding "25% of the spread" there would
# take 4,595 gp a unit, nearly twice the entire profit.

DEFAULT_AGGRESSION = 0.25
DEFAULT_MIN_PROFIT_PER_UNIT = 1


@dataclass
class Suggestion:
    """A concrete pair of prices to place offers at."""

    buy: int               # place your buy offer here
    sell: int              # place your sell offer here
    net_per_unit: int      # profit per unit after tax, guaranteed by arithmetic
    step: int              # gp conceded on each side versus the passive prices
    margin_conceded: float # share of the passive margin actually given up


def suggest_prices(
    low: int | None,
    high: int | None,
    aggression: float = DEFAULT_AGGRESSION,
    min_profit_per_unit: int = DEFAULT_MIN_PROFIT_PER_UNIT,
) -> Suggestion | None:
    """The most aggressive price pair that still clears `min_profit_per_unit`.

    We move both offers inward by the same number of gp (`step`), so the buy
    becomes `low + step` and the sell becomes `high - step`. A bigger step
    means a faster fill and a thinner margin.

    `aggression` is the share of the post-tax margin you are prepared to spend
    on filling sooner, and it sets the ceiling on that step. If the requested
    aggression would push the net profit below the floor, we back off to the
    largest step that still clears it, which is why a viable suggestion may be
    more passive than asked for. Returns None when even the fully passive
    prices cannot clear the floor.

    The returned profit is guaranteed as arithmetic, not as an outcome: it is
    what you make if both offers fill at these prices. Nothing here can
    promise that they will.
    """
    if low is None or high is None or low <= 0:
        return None

    if high <= low:
        return None

    def net_at(step: int) -> int:
        sell = high - step
        buy = low + step
        return sell - ge_tax(sell) - buy

    # The passive margin is the whole budget. If it cannot clear the floor on
    # its own, no step will do better, because profit only falls as the step
    # grows.
    margin = net_at(0)
    if margin < min_profit_per_unit:
        return None

    # Binary search for the largest viable step. Valid because net_at is
    # monotonically non-increasing in step: each extra gp costs 1 on the buy
    # and 1 on the sell, and claws back at most 2% of that on the tax.
    lo_step, hi_step = 0, max(0, int(margin * aggression / 2))
    while lo_step < hi_step:
        mid = (lo_step + hi_step + 1) // 2
        if net_at(mid) >= min_profit_per_unit:
            lo_step = mid
        else:
            hi_step = mid - 1

    net = net_at(lo_step)
    return Suggestion(
        buy=low + lo_step,
        sell=high - lo_step,
        net_per_unit=net,
        step=lo_step,
        # Report what was actually surrendered, measured the same way the
        # budget was set, so the figure and the setting agree.
        margin_conceded=((margin - net) / margin) if margin else 0.0,
    )


def estimate_fill_hours(
    units: int | None,
    hour_low_volume: int,
    hour_high_volume: int,
    volume_fraction: float,
) -> tuple[float | None, float | None, float | None]:
    """Rough hours to buy `units`, to sell them, and for the round trip.

    Which volume fills which offer is the opposite of what the names suggest.
    Your BUY offer is filled by players instant-selling, which is the traffic
    counted in `hour_low_volume`. Your SELL offer is filled by players
    instant-buying, counted in `hour_high_volume`.

    This models flow only. It knows nothing about how many offers are already
    queued ahead of yours, so treat it as an order of magnitude rather than a
    schedule. Returns (buy_hours, sell_hours, total_hours).
    """
    if not units or units <= 0:
        return None, None, None

    buy_flow = hour_low_volume * volume_fraction    # units per hour we can buy
    sell_flow = hour_high_volume * volume_fraction  # units per hour we can sell
    if buy_flow <= 0 or sell_flow <= 0:
        return None, None, None

    buy_hours = units / buy_flow
    sell_hours = units / sell_flow
    return buy_hours, sell_hours, buy_hours + sell_hours


# --- One item's computed flip ------------------------------------------------

# How much wider than the 1h average spread the live spread may be before we
# call it suspicious (a price spike or a manipulation attempt).
DEFAULT_SPREAD_SPIKE_RATIO = 3.0


@dataclass
class Flip:
    """Everything the UI needs about one item."""

    id: int
    name: str
    icon: str
    members: bool
    examine: str
    lowalch: int | None
    highalch: int | None
    value: int | None
    limit: int | None

    # Live prices from /latest.
    low: int | None          # instant-sell: what we buy at
    high: int | None         # instant-buy: what we sell at
    low_time: int | None
    high_time: int | None

    # Hourly aggregates from /1h.
    hour_low_volume: int
    hour_high_volume: int
    hour_avg_low: int | None
    hour_avg_high: int | None

    # Derived flip numbers.
    tax: int | None
    margin: int | None
    roi: float | None            # margin / low, as a fraction
    est_units_per_4h: int | None
    est_profit_per_4h: int | None
    capital_required: int | None

    # Age of the staler of the two price timestamps, in seconds.
    price_age_seconds: int | None

    # Sanity check on the spread.
    hour_avg_spread: int | None
    spread_ratio: float | None
    suspicious_spread: bool

    # Suggested offer prices, and what they are worth.
    suggested_buy: int | None
    suggested_sell: int | None
    suggested_net_per_unit: int | None
    suggested_margin_conceded: float | None
    suggested_profit_per_4h: int | None
    suggested_capital_required: int | None

    # Rough hours for the money to come back, at est_units_per_4h.
    est_hours_to_buy: float | None
    est_hours_to_sell: float | None
    est_hours_total: float | None

    # Profit divided by the round trip, for the quick-flips view.
    est_profit_per_hour: int | None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def estimated_units_per_4h(
    buy_limit: int | None,
    hour_low_volume: int,
    hour_high_volume: int,
    volume_fraction: float,
) -> int:
    """How many units we assume we can actually flip in one 4h limit window.

    We take the smaller of the two sides' hourly volume, because a flip needs
    both a seller to buy from and a buyer to sell to. Scaling by 4 turns an
    hourly figure into a 4h one, then `volume_fraction` is our share of that
    flow. The GE buy limit caps the result.
    """
    tradeable_per_hour = min(hour_low_volume, hour_high_volume)
    our_share = int(tradeable_per_hour * 4 * volume_fraction)
    if buy_limit is None:
        return our_share
    return min(buy_limit, our_share)


def spread_sanity(
    low: int | None,
    high: int | None,
    hour_avg_low: int | None,
    hour_avg_high: int | None,
    spike_ratio: float = DEFAULT_SPREAD_SPIKE_RATIO,
) -> tuple[int | None, float | None, bool]:
    """Compare the live spread against the 1h average spread.

    Returns (hour_avg_spread, ratio, suspicious). A live spread far wider than
    the hourly average usually means a spike or a manipulation attempt rather
    than a real opportunity. With no hourly data to compare against we cannot
    judge, so we do not flag it.
    """
    if low is None or high is None:
        return None, None, False
    if hour_avg_low is None or hour_avg_high is None:
        return None, None, False

    hour_spread = hour_avg_high - hour_avg_low
    live_spread = high - low

    if hour_spread <= 0:
        # The hourly average had no usable spread. Only flag if the live
        # spread claims a real profit out of nowhere.
        return hour_spread, None, live_spread > 0

    ratio = live_spread / hour_spread
    return hour_spread, ratio, ratio > spike_ratio


def build_flip(
    item: dict[str, Any],
    latest: dict[str, Any],
    hourly: dict[str, Any],
    now: int,
    volume_fraction: float,
    spike_ratio: float = DEFAULT_SPREAD_SPIKE_RATIO,
    aggression: float = DEFAULT_AGGRESSION,
    min_profit_per_unit: int = DEFAULT_MIN_PROFIT_PER_UNIT,
) -> Flip:
    """Combine mapping, /latest and /1h for one item into a Flip.

    `item` is a mapping entry; `latest` and `hourly` are that item's entries
    (either may be an empty dict when the API has nothing for it).
    """
    low = latest.get("low")
    high = latest.get("high")
    low_time = latest.get("lowTime")
    high_time = latest.get("highTime")

    hour_low_volume = hourly.get("lowPriceVolume") or 0
    hour_high_volume = hourly.get("highPriceVolume") or 0
    hour_avg_low = hourly.get("avgLowPrice")
    hour_avg_high = hourly.get("avgHighPrice")

    buy_limit = item.get("limit")

    # Derived numbers need both sides of the live price.
    tax = margin = roi = None
    est_units = est_profit = capital = None
    if low is not None and high is not None:
        tax = ge_tax(high)
        margin = high - tax - low
        roi = margin / low if low > 0 else None
        est_units = estimated_units_per_4h(
            buy_limit, hour_low_volume, hour_high_volume, volume_fraction
        )
        est_profit = margin * est_units
        capital = low * est_units

    # Price age is driven by whichever side is staler.
    times = [t for t in (low_time, high_time) if t is not None]
    price_age = (now - min(times)) if times else None

    hour_avg_spread, spread_ratio, suspicious = spread_sanity(
        low, high, hour_avg_low, hour_avg_high, spike_ratio
    )

    # Suggested offer prices, and what they earn over the same unit count.
    suggestion = suggest_prices(low, high, aggression, min_profit_per_unit)
    suggested_buy = suggestion.buy if suggestion else None
    suggested_sell = suggestion.sell if suggestion else None
    suggested_net = suggestion.net_per_unit if suggestion else None
    suggested_conceded = suggestion.margin_conceded if suggestion else None
    suggested_profit = (
        suggested_net * est_units if suggestion and est_units else None
    )
    suggested_capital = (
        suggested_buy * est_units if suggestion and est_units else None
    )

    hours_to_buy, hours_to_sell, hours_total = estimate_fill_hours(
        est_units, hour_low_volume, hour_high_volume, volume_fraction
    )
    per_hour = profit_per_hour(est_profit, hours_total)

    return Flip(
        id=item["id"],
        name=item.get("name", f"Item {item['id']}"),
        icon=item.get("icon", ""),
        members=bool(item.get("members", False)),
        examine=item.get("examine", ""),
        lowalch=item.get("lowalch"),
        highalch=item.get("highalch"),
        value=item.get("value"),
        limit=buy_limit,
        low=low,
        high=high,
        low_time=low_time,
        high_time=high_time,
        hour_low_volume=hour_low_volume,
        hour_high_volume=hour_high_volume,
        hour_avg_low=hour_avg_low,
        hour_avg_high=hour_avg_high,
        tax=tax,
        margin=margin,
        roi=roi,
        est_units_per_4h=est_units,
        est_profit_per_4h=est_profit,
        capital_required=capital,
        price_age_seconds=price_age,
        hour_avg_spread=hour_avg_spread,
        spread_ratio=spread_ratio,
        suspicious_spread=suspicious,
        suggested_buy=suggested_buy,
        suggested_sell=suggested_sell,
        suggested_net_per_unit=suggested_net,
        suggested_margin_conceded=suggested_conceded,
        suggested_profit_per_4h=suggested_profit,
        suggested_capital_required=suggested_capital,
        est_hours_to_buy=hours_to_buy,
        est_hours_to_sell=hours_to_sell,
        est_hours_total=hours_total,
        est_profit_per_hour=per_hour,
    )


def build_all_flips(
    mapping: list[dict[str, Any]],
    latest: dict[str, dict[str, Any]],
    hourly: dict[str, dict[str, Any]],
    now: int,
    volume_fraction: float,
    spike_ratio: float = DEFAULT_SPREAD_SPIKE_RATIO,
    aggression: float = DEFAULT_AGGRESSION,
    min_profit_per_unit: int = DEFAULT_MIN_PROFIT_PER_UNIT,
) -> list[Flip]:
    """Build a Flip for every item in the mapping, best profit first.

    Every item is included, even untradeable ones with no prices, so the
    search box can find anything. The UI filters down from here.
    """
    flips = [
        build_flip(
            item,
            latest.get(str(item["id"]), {}),
            hourly.get(str(item["id"]), {}),
            now,
            volume_fraction,
            spike_ratio,
            aggression,
            min_profit_per_unit,
        )
        for item in mapping
    ]
    return rank_by_profit(flips)


def rank_by_profit(flips: list[Flip]) -> list[Flip]:
    """Sort by estimated 4h profit, descending. Items with no estimate go last."""
    return sorted(
        flips,
        key=lambda f: (f.est_profit_per_4h is not None, f.est_profit_per_4h or 0),
        reverse=True,
    )


# --- Planning a specific trade ----------------------------------------------
# Powers the calculator in the item detail view: you type a buy price, a sell
# price and a quantity, and this works out what you would actually make and how
# long it is likely to take.

# Round-trip hours at or below which a plan earns each rating.
SPEED_BANDS = (("fast", 1.0), ("moderate", 4.0), ("slow", 12.0))
SLOWEST_SPEED = "very slow"


@dataclass
class FlipPlan:
    """The full picture for one hypothetical trade."""

    buy: int
    sell: int
    quantity: int

    tax_per_unit: int
    net_per_unit: int          # profit per item after tax
    total_net: int             # profit across the whole quantity
    total_tax: int
    total_capital: int         # gold you need up front
    roi: float | None

    est_hours_to_buy: float | None
    est_hours_to_sell: float | None
    est_hours_total: float | None
    speed: str                 # one of SPEED_BANDS, SLOWEST_SPEED, or "unknown"
    warnings: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _queue_position(price: int, passive: int, aggressive: int) -> float:
    """How far a price sits from the passive end of the book, in [0, 1].

    0 means you matched the passive price and joined the back of that queue.
    1 means you crossed the spread entirely and should fill at once.
    """
    spread = abs(aggressive - passive)
    if spread <= 0:
        return 0.0
    position = (price - passive) / (aggressive - passive)
    return max(0.0, min(1.0, position))


def _rate_speed(hours: float | None) -> str:
    if hours is None:
        return "unknown"
    for name, ceiling in SPEED_BANDS:
        if hours <= ceiling:
            return name
    return SLOWEST_SPEED


def plan_flip(
    buy: int,
    sell: int,
    quantity: int,
    *,
    low: int | None,
    high: int | None,
    hour_low_volume: int,
    hour_high_volume: int,
    buy_limit: int | None,
    volume_fraction: float,
    suspicious_spread: bool = False,
    price_age_seconds: int | None = None,
) -> FlipPlan:
    """Work out the profit and the likely speed of one specific trade.

    The money side is exact arithmetic. The time side is a heuristic: we start
    from how long the volume alone would take to fill a passive offer, then
    shorten it in proportion to how far your price has crossed toward the other
    end of the spread. It knows nothing about how many offers are already
    queued ahead of yours, so treat it as a rough band, not a schedule.
    """
    warnings: list[str] = []

    quantity = max(0, int(quantity))
    if quantity == 0:
        warnings.append("Quantity is zero.")

    tax_per_unit = ge_tax(sell)
    net_per_unit = sell - tax_per_unit - buy
    total_net = net_per_unit * quantity
    total_tax = tax_per_unit * quantity
    total_capital = buy * quantity
    roi = (net_per_unit / buy) if buy > 0 else None

    # --- money warnings ---
    if buy <= 0:
        # Without a real buy price the profit figures are meaningless, so say
        # that once rather than also commenting on the resulting margin.
        warnings.append("Buy price must be above zero.")
    elif net_per_unit < 0:
        warnings.append(f"Loses {abs(net_per_unit):,} gp a unit after tax.")
    elif net_per_unit == 0:
        warnings.append("Breaks exactly even after tax.")

    # --- position in the book ---
    # A price outside the book on the passive side only fills if the market
    # moves to meet you. Volume says nothing about whether it will, so we
    # refuse to estimate rather than report a falsely cheerful number.
    buy_position = sell_position = 0.0
    outside_book = False
    if low is not None and high is not None and high > low:
        buy_position = _queue_position(buy, low, high)
        sell_position = _queue_position(sell, high, low)
        if buy < low:
            outside_book = True
            warnings.append(
                f"Buy is below the current best bid of {low:,} gp, so it only "
                "fills if the price falls to meet you."
            )
        if sell > high:
            outside_book = True
            warnings.append(
                f"Sell is above the current best ask of {high:,} gp, so it only "
                "fills if the price rises to meet you."
            )

    # --- time ---
    buy_flow = hour_low_volume * volume_fraction     # instant-sellers fill your buy
    sell_flow = hour_high_volume * volume_fraction   # instant-buyers fill your sell
    if outside_book:
        hours_to_buy = hours_to_sell = hours_total = None
    elif quantity > 0 and buy_flow > 0 and sell_flow > 0:
        hours_to_buy = (quantity / buy_flow) * (1.0 - buy_position)
        hours_to_sell = (quantity / sell_flow) * (1.0 - sell_position)
        hours_total = hours_to_buy + hours_to_sell
    else:
        hours_to_buy = hours_to_sell = hours_total = None
        if quantity > 0:
            warnings.append("No recent volume on one side, so this may not fill at all.")

    # --- liquidity and data warnings ---
    if buy_limit is not None and quantity > buy_limit:
        warnings.append(
            f"Quantity exceeds the 4h buy limit of {buy_limit:,}, so this needs "
            "more than one window."
        )
    if suspicious_spread:
        warnings.append(
            "The live spread is far wider than the hourly average, which often "
            "means a spike rather than a real opportunity."
        )
    if price_age_seconds is not None and price_age_seconds > 600:
        warnings.append(
            f"Prices are {price_age_seconds // 60} minutes old and may have moved."
        )

    return FlipPlan(
        buy=buy,
        sell=sell,
        quantity=quantity,
        tax_per_unit=tax_per_unit,
        net_per_unit=net_per_unit,
        total_net=total_net,
        total_tax=total_tax,
        total_capital=total_capital,
        roi=roi,
        est_hours_to_buy=hours_to_buy,
        est_hours_to_sell=hours_to_sell,
        est_hours_total=hours_total,
        speed=_rate_speed(hours_total),
        warnings=warnings,
    )


# --- Price trend and safety --------------------------------------------------
# A wide margin on something that has been sliding for weeks is a trap: the
# price can fall further while you are still holding stock. This reads the
# /timeseries history for one item and turns it into a safety rating.
#
# Thresholds are expressed as a fraction of price per day, so -0.005 means the
# regression line falls half a percent a day, roughly 15% a month. Edit these
# to taste; they are judgement calls, not laws.

TREND_DANGEROUS_SLOPE = -0.005   # falling faster than 0.5% a day
TREND_RISKY_SLOPE = -0.002       # falling faster than 0.2% a day
TREND_CAUTION_SLOPE = -0.0005    # falling at all, noticeably
TREND_RISING_SLOPE = 0.0005      # rising enough to call it a rise

# Daily standard deviation above which a price is jumpy enough to downgrade
# the rating by one band, however the trend looks.
TREND_VOLATILE_STDEV = 0.08      # 8% a day

SAFETY_ORDER = ("dangerous", "risky", "caution", "safe")

# Hours of history each timestep covers per point, used to convert a slope per
# point into a slope per day.
TIMESTEP_HOURS = {"5m": 5 / 60, "1h": 1.0, "6h": 6.0, "24h": 24.0}


@dataclass
class TrendAnalysis:
    """What the price history says about holding this item."""

    timestep: str
    points_used: int
    window_days: float

    change_7d: float | None      # fraction, e.g. -0.04 for a 4% fall
    change_30d: float | None
    change_window: float | None

    slope_per_day: float            # regression gradient, fraction of price a day
    volatility_daily: float         # standard deviation of daily returns
    drawdown_from_peak: float       # how far below the window's peak we sit
    position_in_range: float        # 0 at the window low, 1 at the window high

    direction: str                  # "rising", "flat" or "falling"
    safety: str                     # one of SAFETY_ORDER
    notes: list[str]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _mid_prices(points: list[dict[str, Any]]) -> list[float]:
    """One representative price per point, skipping points with no data.

    Each point carries an average instant-buy and instant-sell price, either of
    which can be null for a quiet period. We take the midpoint when both are
    present, otherwise whichever side traded.
    """
    prices: list[float] = []
    for point in points:
        high = point.get("avgHighPrice")
        low = point.get("avgLowPrice")
        if high and low:
            prices.append((high + low) / 2)
        elif high:
            prices.append(float(high))
        elif low:
            prices.append(float(low))
    return prices


def _least_squares_slope(values: list[float]) -> float:
    """Gradient of the best-fit straight line through `values`, per index step.

    Plain least squares. The x axis is the point index, which works because
    timeseries points are evenly spaced.
    """
    n = len(values)
    if n < 2:
        return 0.0
    mean_x = (n - 1) / 2
    mean_y = sum(values) / n
    numerator = sum((i - mean_x) * (v - mean_y) for i, v in enumerate(values))
    denominator = sum((i - mean_x) ** 2 for i in range(n))
    return numerator / denominator if denominator else 0.0


def _change_over_days(
    prices: list[float], days: float, points_per_day: float
) -> float | None:
    """Fractional price change across the last `days` of the series."""
    span = int(round(days * points_per_day))
    if span < 1 or len(prices) <= span:
        return None
    earlier = prices[-(span + 1)]
    if earlier <= 0:
        return None
    return (prices[-1] - earlier) / earlier


def _daily_volatility(prices: list[float], points_per_day: float) -> float:
    """Standard deviation of returns, scaled from per-point to per-day.

    Returns compound over time, so their spread grows with the square root of
    the number of periods. Multiplying by sqrt(points per day) converts a
    per-point spread into a daily one, which is the usual convention.
    """
    if len(prices) < 3:
        return 0.0
    returns = [
        (prices[i] - prices[i - 1]) / prices[i - 1]
        for i in range(1, len(prices))
        if prices[i - 1] > 0
    ]
    if len(returns) < 2:
        return 0.0
    mean = sum(returns) / len(returns)
    variance = sum((r - mean) ** 2 for r in returns) / (len(returns) - 1)
    return (variance ** 0.5) * (points_per_day ** 0.5)


def _downgrade(safety: str, steps: int = 1) -> str:
    """Move a rating `steps` bands toward "dangerous", stopping at the end."""
    index = SAFETY_ORDER.index(safety)
    return SAFETY_ORDER[max(0, index - steps)]


def analyse_trend(points: list[dict[str, Any]], timestep: str = "24h") -> TrendAnalysis | None:
    """Turn one item's price history into a direction and a safety rating.

    Returns None when there is too little history to say anything useful.
    """
    if timestep not in TIMESTEP_HOURS:
        raise ValueError(f"unknown timestep: {timestep}")

    prices = _mid_prices(points)
    if len(prices) < 5:
        return None

    hours_per_point = TIMESTEP_HOURS[timestep]
    points_per_day = 24 / hours_per_point
    window_days = (len(prices) - 1) * hours_per_point / 24

    mean_price = sum(prices) / len(prices)
    if mean_price <= 0:
        return None

    # Convert the gradient from "price per point" to "fraction of price per day".
    slope_per_point = _least_squares_slope(prices)
    slope_per_day = (slope_per_point * points_per_day) / mean_price

    current = prices[-1]
    peak = max(prices)
    trough = min(prices)
    drawdown = (peak - current) / peak if peak > 0 else 0.0
    spread = peak - trough
    position = (current - trough) / spread if spread > 0 else 0.5

    change_7d = _change_over_days(prices, 7, points_per_day)
    change_30d = _change_over_days(prices, 30, points_per_day)
    change_window = (current - prices[0]) / prices[0] if prices[0] > 0 else None
    volatility = _daily_volatility(prices, points_per_day)

    # --- direction and base rating ---
    if slope_per_day <= TREND_CAUTION_SLOPE:
        direction = "falling"
    elif slope_per_day >= TREND_RISING_SLOPE:
        direction = "rising"
    else:
        direction = "flat"

    if slope_per_day <= TREND_DANGEROUS_SLOPE:
        safety = "dangerous"
    elif slope_per_day <= TREND_RISKY_SLOPE:
        safety = "risky"
    elif slope_per_day <= TREND_CAUTION_SLOPE:
        safety = "caution"
    else:
        safety = "safe"

    notes: list[str] = []

    def signed(fraction: float) -> str:
        return f"{fraction * 100:+.1f}%"

    def described(fraction: float, label: str) -> str:
        """Phrase a change in words, without doubling up the minus sign."""
        size = abs(fraction) * 100
        if size < 0.05:
            return f"Flat over {label}."
        word = "Down" if fraction < 0 else "Up"
        return f"{word} {size:.1f}% over {label}."

    notes.append(
        f"The {window_days:.0f}-day trend line moves {signed(slope_per_day)} a day."
    )
    if change_30d is not None:
        notes.append(described(change_30d, "30 days"))
    if change_7d is not None:
        notes.append(described(change_7d, "7 days"))

    # --- modifiers ---
    if volatility > TREND_VOLATILE_STDEV:
        safety = _downgrade(safety)
        notes.append(
            f"Jumpy: daily swings average {volatility * 100:.0f}%, so the margin "
            "can move while you hold."
        )

    # Falling and already near the floor of its range is the classic trap.
    if direction == "falling" and position < 0.15:
        safety = _downgrade(safety)
        notes.append(
            "Sitting near its lowest price of the window while still falling."
        )

    if drawdown > 0.25 and direction != "rising":
        notes.append(f"Down {drawdown * 100:.0f}% from its peak in this window.")

    if direction == "rising" and safety == "safe":
        notes.append("Rising, so holding stock has been working in your favour.")

    return TrendAnalysis(
        timestep=timestep,
        points_used=len(prices),
        window_days=window_days,
        change_7d=change_7d,
        change_30d=change_30d,
        change_window=change_window,
        slope_per_day=slope_per_day,
        volatility_daily=volatility,
        drawdown_from_peak=drawdown,
        position_in_range=position,
        direction=direction,
        safety=safety,
        notes=notes,
    )


def expected_drift(
    trend: TrendAnalysis | None, price: int, hold_hours: float | None
) -> float | None:
    """Gold per unit the trend alone would move the price over a hold.

    Negative means the price is expected to fall while you hold. This is a
    straight-line extrapolation of a long-run average onto a few hours, which
    is crude, but it answers the question that matters: is the drift large
    enough to matter next to the margin?
    """
    if trend is None or hold_hours is None or price <= 0:
        return None
    return trend.slope_per_day * (hold_hours / 24) * price


# --- Working backwards from a price you have already paid --------------------
# Once you own stock, the question changes. It is no longer "what is the
# margin" but "what must I sell for to not lose money", which the tax makes
# slightly awkward: the tax is charged on the sale, so covering a buy price of
# B needs a sale somewhat above B, and how much above depends on the cap and
# the exemption.


def sell_price_for_profit(buy_price: int, profit_per_unit: int = 0) -> int:
    """Lowest whole sell price that nets at least `profit_per_unit` after tax.

    With `profit_per_unit` left at zero this is the break-even price. Found by
    binary search rather than algebra, because the tax truncates, is capped and
    has an exemption below 50gp, so a closed-form answer would need three cases
    and still round wrongly at the edges.
    """
    if buy_price < 0:
        raise ValueError("buy price cannot be negative")

    def nets_enough(sell: int) -> bool:
        return sell - ge_tax(sell) - buy_price >= profit_per_unit

    # Selling at buy + target + the tax cap always clears, because the tax can
    # never take more than the cap. That makes a safe upper bound.
    low = max(0, buy_price + profit_per_unit)
    high = buy_price + profit_per_unit + GE_TAX_CAP_PER_ITEM + 1

    # Net profit never falls as the sell price rises: each extra gp of price
    # adds at most one gp of tax. So the predicate flips once, and bisecting
    # finds where.
    while low < high:
        mid = (low + high) // 2
        if nets_enough(mid):
            high = mid
        else:
            low = mid + 1
    return low


def break_even_sell_price(buy_price: int) -> int:
    """Lowest sell price that gets your money back after tax."""
    return sell_price_for_profit(buy_price, 0)


@dataclass
class PositionValue:
    """What an open position is worth if you sold into the book right now."""

    break_even: int            # lowest sell price that returns your outlay
    sell_now: int | None    # the live instant-buy price, what you would get
    net_per_unit_now: int | None
    total_now: int | None
    in_profit: bool | None
    gap_to_break_even: int | None  # gp the price must still move to break even
    outlay: int                # what the stock cost you

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def value_position(
    buy_price: int, quantity: int, current_high: int | None
) -> PositionValue:
    """Price up stock you already hold against the current book.

    `current_high` is the live instant-buy price, which is what you receive if
    you dump the stock immediately.
    """
    break_even = break_even_sell_price(buy_price)
    outlay = buy_price * quantity

    if current_high is None:
        return PositionValue(
            break_even=break_even, sell_now=None, net_per_unit_now=None,
            total_now=None, in_profit=None, gap_to_break_even=None, outlay=outlay,
        )

    net_now = current_high - ge_tax(current_high) - buy_price
    return PositionValue(
        break_even=break_even,
        sell_now=current_high,
        net_per_unit_now=net_now,
        total_now=net_now * quantity,
        in_profit=net_now >= 0,
        gap_to_break_even=max(0, break_even - current_high),
        outlay=outlay,
    )


# --- Live trades, bought and sold in pieces ----------------------------------
# A real flip rarely happens in one go. You put an offer in for a few thousand,
# they trickle in, and you list what has arrived in lots as you go, each at
# whatever price looked right at the time. These types track that.

# A margin that has fallen below this share of what it was when the trade was
# opened counts as thinning, and the app says so while you still have units on
# order.
MARGIN_THINNING_RATIO = 0.5

# Where a sell offer sits relative to the live book.
LOT_FILLED = "filled"            # already sold
LOT_IMMEDIATE = "immediate"      # at or under the best bid, should go at once
LOT_COMPETITIVE = "competitive"  # at or under the best ask, near the front
LOT_ABOVE_MARKET = "above market"  # asking more than anyone is currently asking
LOT_UNKNOWN = "unknown"          # no live price to compare against

# What to do about the rest of a buy offer.
BUY_KEEP = "keep"
BUY_THINNING = "thinning"
BUY_STOP = "stop"
BUY_DONE = "done"
BUY_UNKNOWN = "unknown"


@dataclass
class LotValue:
    """One parcel of stock put up for sale, priced against the book."""

    lot_id: str
    quantity: int
    price: int
    sold: bool

    tax_per_unit: int
    net_per_unit: int
    total_net: int
    outlook: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass
class TradeSummary:
    """The whole state of one live trade."""

    buy_price: int | None
    buy_quantity: int
    bought: int
    remaining_to_buy: int
    outlay: int
    break_even: int | None

    units_sold: int
    units_listed: int
    units_unlisted: int

    realised_net: int             # banked, from lots marked sold
    listed_net_if_filled: int     # what the open lots add if they all go
    unlisted_value_now: int | None   # loose stock dumped at the current price
    projected_total: int | None      # all three added up

    current_net_per_unit: int | None    # buying now and dumping now
    reference_net_per_unit: int | None  # the same figure when you started

    buy_advice: str
    advice_reason: str
    lots: list[LotValue]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def lot_outlook(price: int, sold: bool, low: int | None, high: int | None) -> str:
    """How a sell offer at `price` sits against the current book.

    Your sell offer is filled by players instant-buying. Asking below the best
    ask puts you at the front of that queue; asking below the best bid crosses
    it entirely and should go at once.
    """
    if sold:
        return LOT_FILLED
    if low is None or high is None:
        return LOT_UNKNOWN
    if price <= low:
        return LOT_IMMEDIATE
    if price <= high:
        return LOT_COMPETITIVE
    return LOT_ABOVE_MARKET


def value_lot(
    lot_id: str,
    quantity: int,
    price: int,
    sold: bool,
    buy_price: int,
    low: int | None,
    high: int | None,
) -> LotValue:
    """Profit on one lot, after tax, against what the stock cost you."""
    tax = ge_tax(price)
    net = price - tax - buy_price
    return LotValue(
        lot_id=lot_id,
        quantity=quantity,
        price=price,
        sold=sold,
        tax_per_unit=tax,
        net_per_unit=net,
        total_net=net * quantity,
        outlook=lot_outlook(price, sold, low, high),
    )


def advise_on_buying(
    remaining_to_buy: int,
    current_net: int | None,
    reference_net: int | None,
    thinning_ratio: float = MARGIN_THINNING_RATIO,
    unit: str = "unit",
) -> tuple[str, str]:
    """Whether to leave the rest of the buy offer up.

    `unit` names what the figures are per, "unit" for an item and "set" for
    a set trade.

    `current_net` is what you would make per unit buying at your price and
    selling into the book right now. `reference_net` is that same figure when
    the trade was opened, which is what makes "the margin has thinned" a
    statement about this trade rather than a guess.
    """
    if remaining_to_buy <= 0:
        return BUY_DONE, "Your buy offer is filled, so there is nothing left to cancel."
    if current_net is None:
        return BUY_UNKNOWN, "No live price, so there is nothing to compare against."

    if current_net < 0:
        return BUY_STOP, (
            f"Every further {unit} loses {abs(current_net):,} gp: the market now "
            "pays less than you are paying, after tax."
        )
    if current_net == 0:
        return BUY_STOP, f"Further {unit}s break exactly even, so they are not worth the slot."

    if reference_net and reference_net > 0 and current_net < reference_net * thinning_ratio:
        return BUY_THINNING, (
            f"The margin has fallen from {reference_net:,} gp a {unit} when you "
            f"started to {current_net:,} gp now."
        )

    return BUY_KEEP, f"Still worth {current_net:,} gp a {unit} at the current price."


def summarise_trade(
    buy_price: int | None,
    buy_quantity: int,
    bought: int,
    lots: list[dict[str, Any]],
    low: int | None,
    high: int | None,
    reference_sell: int | None = None,
    thinning_ratio: float = MARGIN_THINNING_RATIO,
) -> TradeSummary:
    """Pull one trade's buy, its sell lots and the live book into one picture.

    `lots` are raw dicts with lot_id, quantity, price and sold. `reference_sell`
    is the market's instant-buy price when the trade was opened, kept so the
    advice can talk about how far the margin has moved.
    """
    effective_buy = buy_price or 0

    lot_values = [
        value_lot(
            lot_id=lot["lot_id"],
            quantity=lot["quantity"],
            price=lot["price"],
            sold=bool(lot.get("sold")),
            buy_price=effective_buy,
            low=low,
            high=high,
        )
        for lot in lots
    ]

    units_sold = sum(lot.quantity for lot in lot_values if lot.sold)
    units_listed = sum(lot.quantity for lot in lot_values if not lot.sold)
    units_unlisted = max(0, bought - units_sold - units_listed)

    realised = sum(lot.total_net for lot in lot_values if lot.sold)
    listed_if_filled = sum(lot.total_net for lot in lot_values if not lot.sold)

    # Loose stock is valued at what the book would pay for it this second.
    if high is not None and buy_price is not None:
        net_now = high - ge_tax(high) - buy_price
        unlisted_value = net_now * units_unlisted
        projected = realised + listed_if_filled + unlisted_value
    else:
        net_now = None
        unlisted_value = None
        projected = None

    reference_net = None
    if reference_sell is not None and buy_price is not None:
        reference_net = reference_sell - ge_tax(reference_sell) - buy_price

    remaining = max(0, buy_quantity - bought)
    advice, reason = advise_on_buying(remaining, net_now, reference_net, thinning_ratio)

    return TradeSummary(
        buy_price=buy_price,
        buy_quantity=buy_quantity,
        bought=bought,
        remaining_to_buy=remaining,
        outlay=effective_buy * bought,
        break_even=break_even_sell_price(buy_price) if buy_price is not None else None,
        units_sold=units_sold,
        units_listed=units_listed,
        units_unlisted=units_unlisted,
        realised_net=realised,
        listed_net_if_filled=listed_if_filled,
        unlisted_value_now=unlisted_value,
        projected_total=projected,
        current_net_per_unit=net_now,
        reference_net_per_unit=reference_net,
        buy_advice=advice,
        advice_reason=reason,
        lots=lot_values,
    )


# --- Ranking for the two focused views ---------------------------------------
# The main scanner ranks by profit over a whole four-hour window. That is the
# right question when you are filling a buy limit, but it is the wrong one when
# you care how fast your gold comes back, or when you are buying something to
# hold. These give each of those its own measure.


def profit_per_hour(
    est_profit_per_4h: int | None, est_hours_total: float | None
) -> int | None:
    """Profit divided by how long the round trip takes.

    This is how hard your capital is working, which is what matters when you
    have more gold than patience. Note the buy limit still caps how much you
    can buy in four hours, so a flip that cycles in one hour does not
    necessarily earn four times as much; it frees your gold sooner.
    """
    if est_profit_per_4h is None or not est_hours_total or est_hours_total <= 0:
        return None
    return int(est_profit_per_4h / est_hours_total)


# A long-term buy wants a price that has been climbing for weeks but is cheap
# today. "Cheap" is measured as a pullback from the window's peak rather than a
# position within its whole range: something that has tripled over a year and
# then given back a fifth is a dip worth buying, even though it still sits high
# against where it started.
LONG_TERM_MIN_SLOPE = 0.0005      # rising at least 0.05% a day
LONG_TERM_MIN_DRAWDOWN = 0.05     # and currently at least 5% off its peak


@dataclass
class LongTermView:
    """Why an item does or does not look like something to buy and hold."""

    qualifies: bool
    score: float                 # higher is a stronger buy case
    slope_per_day: float
    drawdown_from_peak: float
    position_in_range: float
    change_30d: float | None
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def assess_long_term(
    trend: TrendAnalysis | None,
    min_slope: float = LONG_TERM_MIN_SLOPE,
    min_drawdown: float = LONG_TERM_MIN_DRAWDOWN,
) -> LongTermView | None:
    """Score an item as something to buy low and hold while the trend carries it.

    Two things have to be true at once. The price must be rising across the
    whole window, otherwise you are catching a falling knife. And it must have
    pulled back from its peak, otherwise you are buying a rise that has already
    happened.

    The score multiplies the two, so a steep climb currently well off its high
    beats a gentle climb sitting at its top. Returns None when there is no
    trend data to judge.
    """
    if trend is None:
        return None

    score = max(0.0, trend.slope_per_day) * max(0.0, trend.drawdown_from_peak)

    rising = trend.slope_per_day >= min_slope
    dipped = trend.drawdown_from_peak >= min_drawdown

    if not rising and not dipped:
        reason = "Flat or falling, and sitting at its high."
    elif not rising:
        reason = "Off its peak, but the trend is flat or falling."
    elif not dipped:
        reason = (
            f"Rising {trend.slope_per_day * 100:+.2f}% a day, but within "
            f"{trend.drawdown_from_peak * 100:.0f}% of its peak, so the rise "
            "has already happened."
        )
    else:
        parts = [
            f"Rising {trend.slope_per_day * 100:+.2f}% a day",
            f"but {trend.drawdown_from_peak * 100:.0f}% below its peak",
        ]
        if trend.change_30d is not None and trend.change_30d < 0:
            parts.append(f"down {abs(trend.change_30d) * 100:.0f}% this month")
        reason = ", ".join(parts) + "."

    return LongTermView(
        qualifies=rising and dipped,
        score=score,
        slope_per_day=trend.slope_per_day,
        drawdown_from_peak=trend.drawdown_from_peak,
        position_in_range=trend.position_in_range,
        change_30d=trend.change_30d,
        reason=reason,
    )
