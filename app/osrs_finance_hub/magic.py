"""Money from Magic: alchemy and enchanting.

Each opportunity is one spell on one item: buy the item, cast, and either take
the coins (alchemy, which pays no GE tax) or sell what it turns into
(enchanting, which does). Pure functions over the per-item Flip numbers, like
sets.py, so prices, volumes, limits and the spike check have one home.

Everything is worked out per item. A bolt spell converts ten bolts a cast, so
its runes are shared across ten.

Rune costs come in two versions, with and without the elemental runes. A
staff, tome or combination staff provides those for free, which is how almost
everyone casts these spells, and the UI lets you pick.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Any

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
from .magic_data import (
    BOLT_ENCHANTS,
    ELEMENTAL_RUNES,
    JEWELLERY_ENCHANTS,
    RUNE_IDS,
    SPELLS,
    TICK_SECONDS,
    Enchant,
    Spell,
)

HIGH_ALCH = "high_alch"
LOW_ALCH = "low_alch"
JEWELLERY = "jewellery"
BOLTS = "bolts"


@dataclass
class RuneCost:
    """What the runes for one item cost, four ways."""

    patient: int | None          # every rune bought at its low
    patient_staff: int | None    # elementals free
    instant: int | None          # every rune bought at its high
    instant_staff: int | None


@dataclass
class MagicOpportunity:
    kind: str                 # HIGH_ALCH, LOW_ALCH, JEWELLERY or BOLTS
    spell: str
    spell_name: str
    level: int
    members: bool

    input_id: int
    input_name: str
    input_icon: str
    input_low: int | None
    input_high: int | None
    limit: int | None

    # What comes out: an item for enchanting, coins for alchemy.
    output_id: int | None
    output_name: str
    output_icon: str
    output_low: int | None
    output_high: int | None
    proceeds: int | None       # per item, after tax: patient sale or alch value
    instant_proceeds: int | None

    rune_cost: RuneCost

    # Per item. "_staff" means the elemental runes were free.
    profit: int | None
    profit_staff: int | None
    instant_profit: int | None
    instant_profit_staff: int | None

    exp_per_item: float
    items_per_hour_casting: int   # if you never ran out of stock
    est_units_per_4h: int | None
    est_profit_per_4h: int | None
    est_profit_per_4h_staff: int | None
    est_hours_casting: float | None   # time spent casting the 4h batch
    est_profit_per_hour_casting: int | None
    est_profit_per_hour_casting_staff: int | None
    capital_required: int | None
    bottleneck: str | None

    price_age_seconds: int | None
    suspicious_spread: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def rune_cost(spell: Spell, flips_by_id: dict[int, Flip]) -> RuneCost:
    """The runes one item costs, for each mix of patience and staff."""
    totals = {"patient": 0, "patient_staff": 0, "instant": 0, "instant_staff": 0}
    known = True
    for rune, count in spell.runes:
        flip = flips_by_id.get(RUNE_IDS[rune])
        if flip is None or flip.low is None or flip.high is None:
            known = False
            break
        free = rune in ELEMENTAL_RUNES
        totals["patient"] += flip.low * count
        totals["instant"] += flip.high * count
        if not free:
            totals["patient_staff"] += flip.low * count
            totals["instant_staff"] += flip.high * count
    if not known:
        return RuneCost(None, None, None, None)
    # Spread across the items one cast converts, rounding up so the cost is
    # never understated.
    per = spell.per_cast
    return RuneCost(**{k: -(-v // per) for k, v in totals.items()})


def items_per_hour(spell: Spell) -> int:
    return int(3600 / (spell.ticks * TICK_SECONDS) * spell.per_cast)


def _minus(a: int | None, *rest: int | None) -> int | None:
    if a is None or any(r is None for r in rest):
        return None
    return a - sum(rest)


def _units(
    limit: int | None, flows: list[tuple[str, int]], volume_fraction: float
) -> tuple[int, str]:
    """Same rule as a flip: our share of the thinnest flow, capped by the limit."""
    name, flow = min(flows, key=lambda f: f[1])
    by_volume = int(flow * 4 * volume_fraction)
    if limit is not None and limit < by_volume:
        return limit, "buy limit"
    return by_volume, name


def build_opportunity(
    kind: str,
    spell: Spell,
    item: Flip,
    output: Flip | None,
    runes: RuneCost,
    volume_fraction: float,
) -> MagicOpportunity:
    """One spell on one item, valued both patiently and instantly."""
    if kind in (HIGH_ALCH, LOW_ALCH):
        value = item.highalch if kind == HIGH_ALCH else item.lowalch
        proceeds = instant_proceeds = value
        output_fields = dict(output_id=None, output_name="Coins", output_icon="",
                             output_low=None, output_high=None)
    else:
        proceeds = None if output.high is None else output.high - ge_tax(output.high)
        instant_proceeds = None if output.low is None else output.low - ge_tax(output.low)
        output_fields = dict(output_id=output.id, output_name=output.name,
                             output_icon=output.icon, output_low=output.low,
                             output_high=output.high)

    profit = _minus(proceeds, item.low, runes.patient)
    profit_staff = _minus(proceeds, item.low, runes.patient_staff)
    instant = _minus(instant_proceeds, item.high, runes.instant)
    instant_staff = _minus(instant_proceeds, item.high, runes.instant_staff)

    # Buying is filled by instant-sells of the input; an enchanted item also
    # has to sell, which instant-buys of the output fill. Alch coins need no
    # buyer at all.
    flows = [(item.name, item.hour_low_volume)]
    if output is not None:
        flows.append((output.name, output.hour_high_volume))
    units, bottleneck = _units(item.limit, flows, volume_fraction)

    # You can only cast so fast. Four hours at the spellbook is the most one
    # buy-limit window can use.
    per_hour = items_per_hour(spell)
    if units > per_hour * 4:
        units, bottleneck = per_hour * 4, "casting speed"
    hours = units / per_hour if units else None
    per_4h = profit * units if profit is not None else None
    per_4h_staff = profit_staff * units if profit_staff is not None else None

    involved = [item] + ([output] if output is not None else [])
    ages = [f.price_age_seconds for f in involved]

    return MagicOpportunity(
        kind=kind,
        spell=spell.key,
        spell_name=spell.name,
        level=spell.level,
        members=spell.members or any(f.members for f in involved),
        input_id=item.id,
        input_name=item.name,
        input_icon=item.icon,
        input_low=item.low,
        input_high=item.high,
        limit=item.limit,
        **output_fields,
        proceeds=proceeds,
        instant_proceeds=instant_proceeds,
        rune_cost=runes,
        profit=profit,
        profit_staff=profit_staff,
        instant_profit=instant,
        instant_profit_staff=instant_staff,
        exp_per_item=spell.exp / spell.per_cast,
        items_per_hour_casting=per_hour,
        est_units_per_4h=units,
        est_profit_per_4h=per_4h,
        est_profit_per_4h_staff=per_4h_staff,
        est_hours_casting=hours,
        # While you have stock, this is what an hour at the spellbook earns.
        est_profit_per_hour_casting=profit_per_hour(per_4h, hours),
        est_profit_per_hour_casting_staff=profit_per_hour(per_4h_staff, hours),
        capital_required=(item.low * units if item.low is not None else None),
        bottleneck=bottleneck,
        price_age_seconds=None if None in ages else max(ages),
        suspicious_spread=any(f.suspicious_spread for f in involved),
    )


def _alch_candidates(
    kind: str, flips: list[Flip], runes: RuneCost, volume_fraction: float
) -> list[MagicOpportunity]:
    """Alchemy is worth listing only where it could pay.

    Every item has an alch value, so listing them all would bury the handful
    that pay under thousands that do not. We keep an item when buying it
    patiently and alching it beats the runes, with a staff for the fire.
    """
    spell = SPELLS[kind]
    out = []
    for item in flips:
        value = item.highalch if kind == HIGH_ALCH else item.lowalch
        if not value or item.low is None or item.high is None:
            continue
        if runes.patient_staff is None or value - item.low - runes.patient_staff <= 0:
            continue
        out.append(build_opportunity(kind, spell, item, None, runes, volume_fraction))
    return out


def _enchant_rows(
    kind: str,
    enchants: tuple[Enchant, ...],
    flips_by_id: dict[int, Flip],
    volume_fraction: float,
) -> list[MagicOpportunity]:
    """Every enchant is listed, worth it or not: there are only a few dozen."""
    out = []
    for e in enchants:
        item, output = flips_by_id.get(e.input_id), flips_by_id.get(e.output_id)
        if item is None or output is None:
            continue
        spell = SPELLS[e.spell]
        out.append(build_opportunity(
            kind, spell, item, output, rune_cost(spell, flips_by_id), volume_fraction,
        ))
    return out


def build_magic_opportunities(
    flips_by_id: dict[int, Flip], volume_fraction: float
) -> list[MagicOpportunity]:
    """Alchemy that pays, and every enchant, best 4h profit with a staff first."""
    flips = list(flips_by_id.values())
    rows: list[MagicOpportunity] = []
    for kind in (HIGH_ALCH, LOW_ALCH):
        runes = rune_cost(SPELLS[kind], flips_by_id)
        rows += _alch_candidates(kind, flips, runes, volume_fraction)
    rows += _enchant_rows(JEWELLERY, JEWELLERY_ENCHANTS, flips_by_id, volume_fraction)
    rows += _enchant_rows(BOLTS, BOLT_ENCHANTS, flips_by_id, volume_fraction)

    return sorted(
        rows,
        key=lambda r: (r.est_profit_per_4h_staff is not None, r.est_profit_per_4h_staff or 0),
        reverse=True,
    )


# --- live magic trades -------------------------------------------------------
# A pinned spell is a trade like any other: buy the item, then either alch it
# (the coins are the sale) or enchant it and sell what it makes in lots.


def spell_kind(spell_key: str) -> str | None:
    if spell_key in (HIGH_ALCH, LOW_ALCH):
        return spell_key
    if spell_key.startswith("enchant_"):
        return JEWELLERY
    if spell_key.startswith("bolts_"):
        return BOLTS
    return None


def find_recipe(spell_key: str, input_id: int) -> tuple[str, Spell, int | None] | None:
    """(kind, spell, output id) for one spell on one item, or None.

    Alchemy works on any item, so it has no output and the caller checks the
    item exists. Enchants must be in the table.
    """
    spell = SPELLS.get(spell_key)
    kind = spell_kind(spell_key)
    if spell is None or kind is None:
        return None
    if kind in (HIGH_ALCH, LOW_ALCH):
        return kind, spell, None
    for e in JEWELLERY_ENCHANTS + BOLT_ENCHANTS:
        if e.spell == spell_key and e.input_id == input_id:
            return kind, spell, e.output_id
    return None


@dataclass
class MagicTradeSummary:
    kind: str
    cost_each: int | None          # buy price plus runes
    buy_quantity: int
    bought: int
    remaining_to_buy: int
    cast: int                         # items alched or enchanted so far
    uncast: int                       # bought but not cast yet
    outlay: int                       # items bought plus runes used so far

    # Enchanting only: what happened to the enchanted items.
    units_sold: int
    units_listed: int
    units_unlisted: int               # enchanted, sitting in the bank
    lots: list[LotValue]

    realised_net: int | None       # alch coins, or sold lots
    listed_net_if_filled: int | None
    unlisted_value_now: int | None  # enchanted or uncast stock at today's price
    projected_total: int | None

    current_profit_each: int | None
    reference_profit_each: int | None
    buy_advice: str
    advice_reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def summarise_magic_trade(
    kind: str,
    buy_price: int | None,
    buy_quantity: int,
    bought: int,
    cast: int,
    rune_cost: int | None,
    lots: list[dict[str, Any]],
    alch_value: int | None = None,
    output_low: int | None = None,
    output_high: int | None = None,
    reference_high: int | None = None,
    thinning_ratio: float = MARGIN_THINNING_RATIO,
) -> MagicTradeSummary:
    """The whole state of one alching or enchanting trade.

    Alchemy pays its fixed value as you cast, so cast items are banked
    profit. Enchanted items are sold in lots like a plain trade, each costing
    the buy price plus its runes.
    """
    alch = kind in (HIGH_ALCH, LOW_ALCH)
    cast = min(max(0, cast), bought)
    uncast = bought - cast
    remaining = max(0, buy_quantity - bought)
    runes = rune_cost or 0
    cost = buy_price + runes if buy_price is not None else None
    outlay = (buy_price or 0) * bought + runes * cast

    def proceeds(high: int | None) -> int | None:
        if alch:
            return alch_value
        return None if high is None else high - ge_tax(high)

    def net(p: int | None) -> int | None:
        return None if p is None or cost is None else p - cost

    current = net(proceeds(output_high))
    reference = net(proceeds(reference_high)) if (alch or reference_high is not None) else None

    lot_values: list[LotValue] = []
    sold = listed = unlisted = 0
    realised = listed_if_filled = unlisted_value = projected = None
    if alch:
        if cost is not None and alch_value is not None:
            realised = cast * current
            unlisted_value = uncast * current
            projected = realised + unlisted_value
    else:
        for lot in lots:
            value = value_lot(
                lot_id=lot["lot_id"], quantity=lot["quantity"], price=lot["price"],
                sold=bool(lot.get("sold")), buy_price=cost or 0,
                low=output_low, high=output_high,
            )
            if cost is None:
                value.net_per_unit = None
                value.total_net = None
            lot_values.append(value)
        sold = sum(lot.quantity for lot in lot_values if lot.sold)
        listed = sum(lot.quantity for lot in lot_values if not lot.sold)
        unlisted = max(0, cast - sold - listed)
        if cost is not None:
            realised = sum(lot.total_net for lot in lot_values if lot.sold)
            listed_if_filled = sum(lot.total_net for lot in lot_values if not lot.sold)
            if current is not None:
                # Enchanted stock and stock still to cast both end up sold.
                unlisted_value = current * (unlisted + uncast)
                projected = realised + listed_if_filled + unlisted_value

    if cost is None and remaining > 0:
        advice, reason = BUY_UNKNOWN, (
            "Record what you are paying for the item to see whether casting still pays."
        )
    else:
        advice, reason = advise_on_buying(
            remaining, current, reference, thinning_ratio, unit="item"
        )

    return MagicTradeSummary(
        kind=kind,
        cost_each=cost,
        buy_quantity=buy_quantity,
        bought=bought,
        remaining_to_buy=remaining,
        cast=cast,
        uncast=uncast,
        outlay=outlay,
        units_sold=sold,
        units_listed=listed,
        units_unlisted=unlisted,
        lots=lot_values,
        realised_net=realised,
        listed_net_if_filled=listed_if_filled,
        unlisted_value_now=unlisted_value,
        projected_total=projected,
        current_profit_each=current,
        reference_profit_each=reference,
        buy_advice=advice,
        advice_reason=reason,
    )
