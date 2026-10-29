"""Item set arbitrage.

A Grand Exchange clerk will swap a set for its pieces, or the pieces for the
set, for free and at once. So when the market prices the two differently there
is a trade either way round:

    unpack  buy the set, exchange it, sell each piece
    pack    buy each piece, exchange them, sell the set

Pure functions over the per-item Flip numbers the scanner already computes,
so prices, volumes, buy limits, price age and the spread spike check all have
one home, in flips.py.

Price semantics are the same as everywhere else: `low` is the instant-sell
price, which a patient buyer pays, and `high` is the instant-buy price, which
a patient seller receives. Each piece is a separate sale, so each pays its own
tax, with its own cap.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, fields
from typing import Any, Optional

from .flips import (
    BUY_UNKNOWN,
    MARGIN_THINNING_RATIO,
    Flip,
    LotValue,
    advise_on_buying,
    ge_tax,
    profit_per_hour,
    value_lot,
)
from .set_data import ITEM_SETS

log = logging.getLogger(__name__)

UNPACK = "unpack"
PACK = "pack"


@dataclass
class SetPiece:
    """One item's live numbers, as a set trade needs them."""

    id: int
    name: str
    icon: str
    low: Optional[int]
    high: Optional[int]
    tax: Optional[int]       # tax on one sold at `high`
    limit: Optional[int]
    hour_low_volume: int     # instant-sells: what fills a buy offer
    hour_high_volume: int    # instant-buys: what fills a sell offer
    price_age_seconds: Optional[int]
    suspicious_spread: bool

    @classmethod
    def from_flip(cls, flip: Flip) -> "SetPiece":
        return cls(
            id=flip.id,
            name=flip.name,
            icon=flip.icon,
            low=flip.low,
            high=flip.high,
            tax=flip.tax,
            limit=flip.limit,
            hour_low_volume=flip.hour_low_volume,
            hour_high_volume=flip.hour_high_volume,
            price_age_seconds=flip.price_age_seconds,
            suspicious_spread=flip.suspicious_spread,
        )


@dataclass
class SetTrade:
    """One direction of a set trade, per set and over a 4h window."""

    direction: str                  # UNPACK or PACK

    # Per set, at the patient prices: buy at low, sell at high.
    cost: Optional[int]
    proceeds: Optional[int]         # after tax
    tax: Optional[int]
    profit: Optional[int]
    roi: Optional[float]

    # Per set, crossing the spread on both sides right now: buy at high, sell
    # at low. Usually negative. When it is positive the gap is wide enough to
    # take without waiting in any queue.
    instant_profit: Optional[int]

    est_units_per_4h: Optional[int]
    est_profit_per_4h: Optional[int]
    capital_required: Optional[int]
    est_hours_to_buy: Optional[float]
    est_hours_to_sell: Optional[float]
    est_hours_total: Optional[float]
    est_profit_per_hour: Optional[int]

    # What caps the unit count: "buy limit" or the item whose volume is thinnest.
    bottleneck: Optional[str]


@dataclass
class SetFlip:
    """A set, its pieces, and both ways of trading the gap between them."""

    id: int
    name: str
    icon: str
    category: str
    members: bool
    set: SetPiece
    pieces: list[SetPiece]
    unpack: SetTrade
    pack: SetTrade
    price_age_seconds: Optional[int]   # the stalest price involved
    suspicious_spread: bool            # any item involved looks spiked

    def best(self) -> SetTrade:
        """Whichever direction earns more over a 4h window."""
        a = self.unpack.est_profit_per_4h
        b = self.pack.est_profit_per_4h
        if b is not None and (a is None or b > a):
            return self.pack
        return self.unpack

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["best_direction"] = self.best().direction
        return out


def set_legs(set_id: int, direction: str, item_sets=ITEM_SETS) -> Optional[tuple[list[int], list[int]]]:
    """(ids you buy, ids you sell) for a set trade, or None for an unknown set."""
    for _, (sid, _), components in item_sets:
        if sid == set_id:
            pieces = [cid for cid, _ in components]
            if direction == UNPACK:
                return [set_id], pieces
            if direction == PACK:
                return pieces, [set_id]
            return None
    return None


def _empty_trade(direction: str) -> SetTrade:
    blanks = {f.name: None for f in fields(SetTrade) if f.name != "direction"}
    return SetTrade(direction=direction, **blanks)


def _units_and_bottleneck(
    limit: Optional[int],
    flows: list[tuple[str, int]],
    volume_fraction: float,
) -> tuple[int, str]:
    """Units we can push through a 4h window, and what stops us doing more.

    `flows` is every (name, hourly volume) the trade depends on. The thinnest
    one is the real constraint, exactly as with a single item: you cannot
    unpack sets faster than the slowest piece sells.
    """
    name, flow = min(flows, key=lambda f: f[1])
    by_volume = int(flow * 4 * volume_fraction)
    if limit is not None and limit < by_volume:
        return limit, "buy limit"
    return by_volume, name


def _hours(units: int, flow: int, volume_fraction: float) -> Optional[float]:
    rate = flow * volume_fraction
    if units <= 0 or rate <= 0:
        return None
    return units / rate


def _trade(
    direction: str,
    cost: int,
    proceeds: int,
    tax: int,
    instant_profit: int,
    limit: Optional[int],
    buy_flows: list[tuple[str, int]],
    sell_flows: list[tuple[str, int]],
    volume_fraction: float,
) -> SetTrade:
    profit = proceeds - cost
    units, bottleneck = _units_and_bottleneck(
        limit, buy_flows + sell_flows, volume_fraction
    )

    # Pieces are bought, or sold, in parallel GE slots, so each side takes as
    # long as its thinnest item. The two sides run one after the other.
    to_buy = _hours(units, min(f for _, f in buy_flows), volume_fraction)
    to_sell = _hours(units, min(f for _, f in sell_flows), volume_fraction)
    total = to_buy + to_sell if to_buy is not None and to_sell is not None else None
    per_4h = profit * units

    return SetTrade(
        direction=direction,
        cost=cost,
        proceeds=proceeds,
        tax=tax,
        profit=profit,
        roi=profit / cost if cost > 0 else None,
        instant_profit=instant_profit,
        est_units_per_4h=units,
        est_profit_per_4h=per_4h,
        capital_required=cost * units,
        est_hours_to_buy=to_buy,
        est_hours_to_sell=to_sell,
        est_hours_total=total,
        est_profit_per_hour=profit_per_hour(per_4h, total),
        bottleneck=bottleneck,
    )


def unpack_trade(s: SetPiece, pieces: list[SetPiece], volume_fraction: float) -> SetTrade:
    """Buy the set at its low, sell every piece at its high."""
    if s.low is None or s.high is None or any(
        p.low is None or p.high is None for p in pieces
    ):
        return _empty_trade(UNPACK)

    tax = sum(ge_tax(p.high) for p in pieces)
    proceeds = sum(p.high for p in pieces) - tax
    instant = sum(p.low - ge_tax(p.low) for p in pieces) - s.high

    return _trade(
        UNPACK,
        cost=s.low,
        proceeds=proceeds,
        tax=tax,
        instant_profit=instant,
        limit=s.limit,
        buy_flows=[(s.name, s.hour_low_volume)],
        sell_flows=[(p.name, p.hour_high_volume) for p in pieces],
        volume_fraction=volume_fraction,
    )


def pack_trade(s: SetPiece, pieces: list[SetPiece], volume_fraction: float) -> SetTrade:
    """Buy every piece at its low, sell the set at its high."""
    if s.low is None or s.high is None or any(
        p.low is None or p.high is None for p in pieces
    ):
        return _empty_trade(PACK)

    tax = ge_tax(s.high)
    instant = s.low - ge_tax(s.low) - sum(p.high for p in pieces)

    # One of each piece makes a set, so the tightest buy limit is the set's.
    limits = [p.limit for p in pieces if p.limit is not None]

    return _trade(
        PACK,
        cost=sum(p.low for p in pieces),
        proceeds=s.high - tax,
        tax=tax,
        instant_profit=instant,
        limit=min(limits) if limits else None,
        buy_flows=[(p.name, p.hour_low_volume) for p in pieces],
        sell_flows=[(s.name, s.hour_high_volume)],
        volume_fraction=volume_fraction,
    )


def build_set_flips(
    flips_by_id: dict[int, Flip],
    volume_fraction: float,
    item_sets=ITEM_SETS,
) -> list[SetFlip]:
    """Every known set, best 4h profit in either direction first."""
    out: list[SetFlip] = []
    for category, (set_id, set_name), components in item_sets:
        set_flip = flips_by_id.get(set_id)
        piece_flips = [flips_by_id.get(cid) for cid, _ in components]
        if set_flip is None or any(p is None for p in piece_flips):
            missing = [set_name] if set_flip is None else []
            missing += [n for (cid, n), p in zip(components, piece_flips) if p is None]
            log.warning("skipping %s: not in /mapping: %s", set_name, ", ".join(missing))
            continue

        s = SetPiece.from_flip(set_flip)
        pieces = [SetPiece.from_flip(p) for p in piece_flips]
        everything = [s, *pieces]
        ages = [p.price_age_seconds for p in everything]

        out.append(SetFlip(
            id=set_id,
            name=set_flip.name,
            icon=set_flip.icon,
            category=category,
            members=set_flip.members,
            set=s,
            pieces=pieces,
            unpack=unpack_trade(s, pieces, volume_fraction),
            pack=pack_trade(s, pieces, volume_fraction),
            price_age_seconds=None if None in ages else max(ages),
            suspicious_spread=any(p.suspicious_spread for p in everything),
        ))

    return sorted(
        out,
        key=lambda f: (f.best().est_profit_per_4h is not None,
                       f.best().est_profit_per_4h or 0),
        reverse=True,
    )


# --- Live set trades ----------------------------------------------------------
# A pinned set trade, valued the same way as a plain one. The difference is
# that what you buy and what you sell are different items, joined by the free
# exchange at the clerk. Complete sets are what you can sell: when packing,
# the piece you have least of decides how many sets you can make.


@dataclass
class SetBuyLegValue:
    item_id: int
    buy_price: Optional[int]
    buy_quantity: int
    bought: int
    remaining_to_buy: int
    outlay: int
    spare: int        # bought, but waiting for the other pieces to make a set


@dataclass
class SetSellLegValue:
    item_id: int
    cost_basis: Optional[int]     # what one of these cost you, per unit
    units_sold: int
    units_listed: int
    units_unlisted: int
    net_now_per_unit: Optional[int]   # selling one into the book right now
    lots: list[LotValue]


@dataclass
class SetTradeSummary:
    direction: str
    complete_sets: int
    sets_ordered: int
    remaining_to_buy: int
    cost_per_set: Optional[int]       # None until every buy price is recorded
    outlay: int

    realised_net: Optional[int]
    listed_net_if_filled: Optional[int]
    unlisted_value_now: Optional[int]
    projected_total: Optional[int]

    current_profit_per_set: Optional[int]
    reference_profit_per_set: Optional[int]
    buy_advice: str
    advice_reason: str

    buys: list[SetBuyLegValue]
    sells: list[SetSellLegValue]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _after_tax(price: Optional[int]) -> Optional[int]:
    return None if price is None else price - ge_tax(price)


def split_set_cost(
    set_cost: int, piece_ids: list[int], weights: dict[int, Optional[int]]
) -> dict[int, int]:
    """Share what a set cost across its pieces, so each piece has a cost.

    Unpacking turns one purchase into several sales, so the only fair way to
    give each sale a profit is to divide the set's cost between the pieces.
    We split in proportion to what each piece was worth (`weights`), which
    puts most of the cost on the piece that brings most of the money back.
    Without a usable weight for every piece the split is even. The shares
    always add up to exactly `set_cost`.
    """
    usable = [weights.get(i) for i in piece_ids]
    if any(w is None or w <= 0 for w in usable):
        usable = [1] * len(piece_ids)
    total = sum(usable)

    shares = {i: set_cost * w // total for i, w in zip(piece_ids, usable)}
    # Integer division leaves a few gp over; give them to the dearest piece.
    leftover = set_cost - sum(shares.values())
    dearest = max(zip(piece_ids, usable), key=lambda pair: pair[1])[0]
    shares[dearest] += leftover
    return shares


def summarise_set_trade(
    direction: str,
    buys: list[dict[str, Any]],
    sells: list[dict[str, Any]],
    prices: dict[int, tuple[Optional[int], Optional[int]]],
    reference_highs: Optional[dict[int, int]] = None,
    thinning_ratio: float = MARGIN_THINNING_RATIO,
) -> SetTradeSummary:
    """The whole state of one set trade, valued against the live book.

    `buys` are dicts with item_id, buy_price, buy_quantity and bought.
    `sells` are dicts with item_id and lots. `prices` maps every item id to
    its live (low, high). `reference_highs` is each item's instant-buy price
    when the trade was opened.
    """
    reference_highs = reference_highs or {}
    unpack = direction == UNPACK

    # --- buying ---
    complete = min((b["bought"] for b in buys), default=0)
    buy_values = [
        SetBuyLegValue(
            item_id=b["item_id"],
            buy_price=b.get("buy_price"),
            buy_quantity=b["buy_quantity"],
            bought=b["bought"],
            remaining_to_buy=max(0, b["buy_quantity"] - b["bought"]),
            outlay=(b.get("buy_price") or 0) * b["bought"],
            spare=b["bought"] - complete,
        )
        for b in buys
    ]
    prices_known = all(b.buy_price is not None for b in buy_values)
    cost_per_set = sum(b.buy_price for b in buy_values) if prices_known else None

    # --- what each thing you sell cost you ---
    sell_ids = [s["item_id"] for s in sells]
    if cost_per_set is None:
        basis: dict[int, Optional[int]] = {i: None for i in sell_ids}
    elif unpack:
        weights = {i: reference_highs.get(i) or prices.get(i, (None, None))[1] for i in sell_ids}
        basis = split_set_cost(cost_per_set, sell_ids, weights)
    else:
        basis = {sell_ids[0]: cost_per_set}

    # --- selling ---
    sell_values = []
    for s in sells:
        item_id = s["item_id"]
        low, high = prices.get(item_id, (None, None))
        cost = basis[item_id]
        lots = []
        for lot in s["lots"]:
            value = value_lot(
                lot_id=lot["lot_id"], quantity=lot["quantity"], price=lot["price"],
                sold=bool(lot.get("sold")), buy_price=cost or 0, low=low, high=high,
            )
            if cost is None:
                # Without a cost there is no profit to show, only a price.
                value.net_per_unit = None
                value.total_net = None
            lots.append(value)

        sold = sum(l.quantity for l in lots if l.sold)
        listed = sum(l.quantity for l in lots if not l.sold)
        net_now = None
        if cost is not None and high is not None:
            net_now = _after_tax(high) - cost
        sell_values.append(SetSellLegValue(
            item_id=item_id,
            cost_basis=cost,
            units_sold=sold,
            units_listed=listed,
            units_unlisted=max(0, complete - sold - listed),
            net_now_per_unit=net_now,
            lots=lots,
        ))

    # --- totals, only once every cost is known ---
    realised = listed_if_filled = unlisted_value = projected = None
    if cost_per_set is not None:
        realised = sum(l.total_net for s in sell_values for l in s.lots if l.sold)
        listed_if_filled = sum(l.total_net for s in sell_values for l in s.lots if not l.sold)
        if all(s.net_now_per_unit is not None for s in sell_values):
            unlisted_value = sum(s.net_now_per_unit * s.units_unlisted for s in sell_values)
            projected = realised + listed_if_filled + unlisted_value

    # --- should you keep buying? ---
    def profit_per_set(highs: dict[int, Optional[int]]) -> Optional[int]:
        if cost_per_set is None:
            return None
        proceeds = [_after_tax(highs.get(i)) for i in sell_ids]
        if any(p is None for p in proceeds):
            return None
        return sum(proceeds) - cost_per_set

    current = profit_per_set({i: prices.get(i, (None, None))[1] for i in sell_ids})
    reference = profit_per_set(reference_highs) if reference_highs else None
    remaining = max((b.remaining_to_buy for b in buy_values), default=0)

    if cost_per_set is None and remaining > 0:
        advice, reason = BUY_UNKNOWN, (
            "Record what you are paying for every item you are buying to see "
            "whether the trade still pays."
        )
    else:
        advice, reason = advise_on_buying(
            remaining, current, reference, thinning_ratio, unit="set"
        )

    return SetTradeSummary(
        direction=direction,
        complete_sets=complete,
        sets_ordered=min((b.buy_quantity for b in buy_values), default=0),
        remaining_to_buy=remaining,
        cost_per_set=cost_per_set,
        outlay=sum(b.outlay for b in buy_values),
        realised_net=realised,
        listed_net_if_filled=listed_if_filled,
        unlisted_value_now=unlisted_value,
        projected_total=projected,
        current_profit_per_set=current,
        reference_profit_per_set=reference,
        buy_advice=advice,
        advice_reason=reason,
        buys=buy_values,
        sells=sell_values,
    )
