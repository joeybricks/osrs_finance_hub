"""Pinned items and open positions, saved to a small JSON file.

This is the one piece of state the app owns rather than borrows from the API.
It is your own record of what you have bought, so it lives on disk rather than
in the browser: clearing site data should not lose track of your gold.

The file is tiny and written whole on every change, which is fine for a
single-user local app. Writes go to a temporary file and are then renamed, so
an interrupted save cannot leave a half-written file behind.
"""

from __future__ import annotations

import json
import logging
import os
import shutil
import tempfile
import time
import uuid
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STORE_VERSION = 4   # 3 added set trades, 4 added magic trades


@dataclass
class SellLot:
    """One parcel of stock put up for sale.

    Flips are sold in pieces: a few thousand arrive, you list them, more
    arrive, you list those at whatever price looks right then. Each of those
    listings is a lot, with its own price and its own profit.
    """

    lot_id: str
    quantity: int
    price: int
    sold: bool = False
    created_at: float = field(default_factory=time.time)
    sold_at: float | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SellLot:
        return cls(
            lot_id=str(raw["lot_id"]),
            quantity=max(0, int(raw.get("quantity") or 0)),
            price=max(0, int(raw.get("price") or 0)),
            sold=bool(raw.get("sold")),
            created_at=float(raw.get("created_at") or time.time()),
            sold_at=(float(raw["sold_at"]) if raw.get("sold_at") is not None else None),
        )


def new_lot_id() -> str:
    """Short, unique enough for a personal list of open offers."""
    return uuid.uuid4().hex[:8]


@dataclass
class Position:
    """One pinned item, and the live trade on it if you have started one.

    A pin with no `buy_price` is just a watch. Once you record what you offered
    it becomes a trade: `buy_quantity` is what you put on the offer, `bought`
    is how much has actually arrived, and `lots` is what you have listed back
    up for sale so far.
    """

    item_id: int
    buy_price: int | None = None
    buy_quantity: int = 0
    bought: int = 0
    reference_sell: int | None = None
    lots: list[SellLot] = field(default_factory=list)
    note: str = ""
    pinned_at: float = field(default_factory=time.time)

    @property
    def is_open_position(self) -> bool:
        return self.buy_price is not None and (self.buy_quantity > 0 or self.bought > 0)

    def lot(self, lot_id: str) -> SellLot | None:
        return next((lot for lot in self.lots if lot.lot_id == lot_id), None)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> Position:
        """Read one entry, upgrading the version 1 shape as we go.

        Version 1 had a single `quantity` and an optional `target_sell`. The
        quantity becomes both what was ordered and what arrived, since the old
        format could not tell them apart, and a target price becomes a lot for
        the whole amount, which is what it meant in practice.
        """
        lots = [SellLot.from_dict(lot) for lot in raw.get("lots", [])]

        if "buy_quantity" in raw or "bought" in raw:
            buy_quantity = max(0, int(raw.get("buy_quantity") or 0))
            bought = max(0, int(raw.get("bought") or 0))
        else:
            legacy = max(0, int(raw.get("quantity") or 0))
            buy_quantity = bought = legacy
            target = raw.get("target_sell")
            if target and legacy:
                lots.append(SellLot(lot_id=new_lot_id(), quantity=legacy, price=int(target)))

        return cls(
            item_id=int(raw["item_id"]),
            buy_price=(int(raw["buy_price"]) if raw.get("buy_price") is not None else None),
            buy_quantity=buy_quantity,
            bought=bought,
            reference_sell=(
                int(raw["reference_sell"]) if raw.get("reference_sell") is not None else None
            ),
            lots=lots,
            note=str(raw.get("note") or ""),
            pinned_at=float(raw.get("pinned_at") or time.time()),
        )


@dataclass
class SetBuyLeg:
    """One item you are buying for a set trade: the set when unpacking, or
    each piece when packing. Same three numbers as a plain trade."""

    item_id: int
    buy_price: int | None = None
    buy_quantity: int = 0
    bought: int = 0

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SetBuyLeg:
        return cls(
            item_id=int(raw["item_id"]),
            buy_price=(int(raw["buy_price"]) if raw.get("buy_price") is not None else None),
            buy_quantity=max(0, int(raw.get("buy_quantity") or 0)),
            bought=max(0, int(raw.get("bought") or 0)),
        )


@dataclass
class SetSellLeg:
    """One item you are selling from a set trade, in lots like any other."""

    item_id: int
    lots: list[SellLot] = field(default_factory=list)

    def lot(self, lot_id: str) -> SellLot | None:
        return next((lot for lot in self.lots if lot.lot_id == lot_id), None)

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SetSellLeg:
        return cls(
            item_id=int(raw["item_id"]),
            lots=[SellLot.from_dict(lot) for lot in raw.get("lots", [])],
        )


@dataclass
class SetPosition:
    """A pinned set trade.

    Packing buys every piece and sells the set; unpacking buys the set and
    sells every piece. The legs are fixed when it is pinned, so the record
    stands on its own even if the set table changes later.

    `reference_highs` snapshots every item's instant-buy price when the first
    buy price is recorded. It does two jobs: it lets the advice say how far
    the profit per set has moved since you started, and when unpacking it
    splits what the set cost you across the pieces, so each piece's lots can
    show a profit of their own.
    """

    set_id: int
    direction: str
    buys: list[SetBuyLeg]
    sells: list[SetSellLeg]
    reference_highs: dict[int, int] = field(default_factory=dict)
    pinned_at: float = field(default_factory=time.time)

    def buy_leg(self, item_id: int) -> SetBuyLeg | None:
        return next((leg for leg in self.buys if leg.item_id == item_id), None)

    def sell_leg(self, item_id: int) -> SetSellLeg | None:
        return next((leg for leg in self.sells if leg.item_id == item_id), None)

    @property
    def has_buy_price(self) -> bool:
        return any(leg.buy_price is not None for leg in self.buys)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        # JSON object keys are strings; say so here rather than relying on json.
        out["reference_highs"] = {str(k): v for k, v in self.reference_highs.items()}
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> SetPosition:
        direction = str(raw["direction"])
        if direction not in ("pack", "unpack"):
            raise ValueError(f"unknown set trade direction {direction!r}")
        return cls(
            set_id=int(raw["set_id"]),
            direction=direction,
            buys=[SetBuyLeg.from_dict(leg) for leg in raw.get("buys", [])],
            sells=[SetSellLeg.from_dict(leg) for leg in raw.get("sells", [])],
            reference_highs={
                int(k): int(v) for k, v in (raw.get("reference_highs") or {}).items()
            },
            pinned_at=float(raw.get("pinned_at") or time.time()),
        )


def magic_key(spell: str, input_id: int) -> str:
    return f"{spell}:{input_id}"


@dataclass
class MagicPosition:
    """A pinned spell on one item: buy it, cast, and bank the coins or sell
    what it makes.

    `cast` counts items alched or enchanted so far. `rune_cost` is what the
    runes for one item cost, taken from the market when pinned (with or
    without elemental runes, as you chose) and yours to correct. Enchanted
    items are sold in `lots`; alchemy has none. `reference_high` snapshots
    the enchanted item's instant-buy price when the first buy price is
    recorded, so the advice can say how far the margin has moved.
    """

    spell: str
    input_id: int
    output_id: int | None = None
    staff: bool = True
    rune_cost: int | None = None
    buy_price: int | None = None
    buy_quantity: int = 0
    bought: int = 0
    cast: int = 0
    reference_high: int | None = None
    lots: list[SellLot] = field(default_factory=list)
    pinned_at: float = field(default_factory=time.time)

    @property
    def key(self) -> str:
        return magic_key(self.spell, self.input_id)

    @property
    def started(self) -> bool:
        return self.buy_price is not None or self.bought > 0

    def lot(self, lot_id: str) -> SellLot | None:
        return next((lot for lot in self.lots if lot.lot_id == lot_id), None)

    def to_dict(self) -> dict[str, Any]:
        out = asdict(self)
        out["key"] = self.key
        return out

    @classmethod
    def from_dict(cls, raw: dict[str, Any]) -> MagicPosition:
        def opt(name: str) -> int | None:
            return int(raw[name]) if raw.get(name) is not None else None

        return cls(
            spell=str(raw["spell"]),
            input_id=int(raw["input_id"]),
            output_id=opt("output_id"),
            staff=bool(raw.get("staff", True)),
            rune_cost=opt("rune_cost"),
            buy_price=opt("buy_price"),
            buy_quantity=max(0, int(raw.get("buy_quantity") or 0)),
            bought=max(0, int(raw.get("bought") or 0)),
            cast=max(0, int(raw.get("cast") or 0)),
            reference_high=opt("reference_high"),
            lots=[SellLot.from_dict(lot) for lot in raw.get("lots", [])],
            pinned_at=float(raw.get("pinned_at") or time.time()),
        )


def _change_lot(
    lot: SellLot, quantity: int | None, price: int | None, sold: bool | None
) -> None:
    if quantity is not None:
        lot.quantity = max(0, quantity)
    if price is not None:
        lot.price = max(0, price)
    if sold is not None and sold != lot.sold:
        lot.sold = sold
        lot.sold_at = time.time() if sold else None


def adopt_legacy_file(path: Path, legacy: Path) -> bool:
    """Copy the trades file from the app's old name, once.

    Only when there is nothing at the new path yet, and only a copy: the old
    file stays where it was as a backup. Returns whether a copy was made.
    """
    if path.exists() or not legacy.exists():
        return False
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(legacy, path)
    except OSError as exc:
        log.warning("could not copy trades from %s to %s: %s", legacy, path, exc)
        return False
    log.info("copied trades from %s to %s; the old file is left as a backup", legacy, path)
    return True


class PositionStore:
    """Load, mutate and save the pinned list.

    Kept in memory and flushed on every change. A corrupt or unreadable file is
    reported and treated as empty rather than crashing the app, because losing
    the pin list should never stop you seeing prices.
    """

    def __init__(self, path: Path) -> None:
        self.path = path
        self._positions: dict[int, Position] = {}
        self._sets: dict[int, SetPosition] = {}
        self._magic: dict[str, MagicPosition] = {}
        self.load_error: str | None = None
        self.load()

    # --- reading -------------------------------------------------------------

    def all(self) -> list[Position]:
        """Pinned items, most recently pinned first."""
        return sorted(self._positions.values(), key=lambda p: p.pinned_at, reverse=True)

    def get(self, item_id: int) -> Position | None:
        return self._positions.get(item_id)

    def __len__(self) -> int:
        return len(self._positions)

    # --- writing -------------------------------------------------------------

    def upsert(
        self,
        item_id: int,
        buy_price: int | None = None,
        buy_quantity: int | None = None,
        bought: int | None = None,
        reference_sell: int | None = None,
        note: str | None = None,
        clear_buy: bool = False,
    ) -> Position:
        """Pin an item, or change the details of a trade already on it.

        Only the fields you pass are touched, so the UI can save how many have
        arrived without having to resend everything else. `clear_buy` throws
        the trade away and leaves a plain watch.
        """
        position = self._positions.get(item_id) or Position(item_id=item_id)

        if buy_price is not None:
            position.buy_price = max(0, buy_price)
        if buy_quantity is not None:
            position.buy_quantity = max(0, buy_quantity)
        if bought is not None:
            position.bought = max(0, bought)
        if reference_sell is not None:
            position.reference_sell = max(0, reference_sell)
        if note is not None:
            position.note = note[:500]
        if clear_buy:
            position.buy_price = None
            position.buy_quantity = 0
            position.bought = 0
            position.reference_sell = None
            position.lots = []

        self._positions[item_id] = position
        self.save()
        return position

    # --- sell lots -----------------------------------------------------------

    def add_lot(self, item_id: int, quantity: int, price: int) -> SellLot | None:
        """List a parcel of stock for sale. Returns None if nothing is pinned."""
        position = self._positions.get(item_id)
        if position is None:
            return None
        lot = SellLot(lot_id=new_lot_id(), quantity=max(0, quantity), price=max(0, price))
        position.lots.append(lot)
        self.save()
        return lot

    def update_lot(
        self,
        item_id: int,
        lot_id: str,
        quantity: int | None = None,
        price: int | None = None,
        sold: bool | None = None,
    ) -> SellLot | None:
        """Change a lot, most often to mark it sold. None if it is not there."""
        position = self._positions.get(item_id)
        if position is None:
            return None
        lot = position.lot(lot_id)
        if lot is None:
            return None

        if quantity is not None:
            lot.quantity = max(0, quantity)
        if price is not None:
            lot.price = max(0, price)
        if sold is not None and sold != lot.sold:
            lot.sold = sold
            lot.sold_at = time.time() if sold else None

        self.save()
        return lot

    def remove_lot(self, item_id: int, lot_id: str) -> bool:
        """Cancel a listing. Returns whether there was one to cancel."""
        position = self._positions.get(item_id)
        if position is None:
            return False
        before = len(position.lots)
        position.lots = [lot for lot in position.lots if lot.lot_id != lot_id]
        if len(position.lots) == before:
            return False
        self.save()
        return True

    def remove(self, item_id: int) -> bool:
        """Unpin. Returns whether anything was actually pinned."""
        if item_id not in self._positions:
            return False
        del self._positions[item_id]
        self.save()
        return True

    # --- set trades ----------------------------------------------------------
    # Keyed by the set's item id. One trade per set: to switch direction,
    # unpin and pin again.

    def all_sets(self) -> list[SetPosition]:
        """Pinned set trades, most recently pinned first."""
        return sorted(self._sets.values(), key=lambda p: p.pinned_at, reverse=True)

    def get_set(self, set_id: int) -> SetPosition | None:
        return self._sets.get(set_id)

    def pin_set(
        self, set_id: int, direction: str, buy_ids: list[int], sell_ids: list[int]
    ) -> SetPosition:
        """Pin a set trade. Pinning one already pinned leaves it untouched."""
        existing = self._sets.get(set_id)
        if existing is not None:
            return existing
        position = SetPosition(
            set_id=set_id,
            direction=direction,
            buys=[SetBuyLeg(item_id=i) for i in buy_ids],
            sells=[SetSellLeg(item_id=i) for i in sell_ids],
        )
        self._sets[set_id] = position
        self.save()
        return position

    def remove_set(self, set_id: int) -> bool:
        if self._sets.pop(set_id, None) is None:
            return False
        self.save()
        return True

    def update_set_buy(
        self,
        set_id: int,
        item_id: int,
        buy_price: int | None = None,
        buy_quantity: int | None = None,
        bought: int | None = None,
        reference_highs: dict[int, int] | None = None,
    ) -> SetBuyLeg | None:
        """Change one buy leg. Only the fields passed are touched.

        `reference_highs` is only stored if there is no snapshot yet, so the
        baseline stays the moment the trade was opened.
        """
        position = self._sets.get(set_id)
        leg = position.buy_leg(item_id) if position else None
        if leg is None:
            return None
        if buy_price is not None:
            leg.buy_price = max(0, buy_price)
        if buy_quantity is not None:
            leg.buy_quantity = max(0, buy_quantity)
        if bought is not None:
            leg.bought = max(0, bought)
        if reference_highs and not position.reference_highs:
            position.reference_highs = dict(reference_highs)
        self.save()
        return leg

    def add_set_lot(
        self, set_id: int, item_id: int, quantity: int, price: int
    ) -> SellLot | None:
        position = self._sets.get(set_id)
        leg = position.sell_leg(item_id) if position else None
        if leg is None:
            return None
        lot = SellLot(lot_id=new_lot_id(), quantity=max(0, quantity), price=max(0, price))
        leg.lots.append(lot)
        self.save()
        return lot

    def update_set_lot(
        self,
        set_id: int,
        item_id: int,
        lot_id: str,
        quantity: int | None = None,
        price: int | None = None,
        sold: bool | None = None,
    ) -> SellLot | None:
        position = self._sets.get(set_id)
        leg = position.sell_leg(item_id) if position else None
        lot = leg.lot(lot_id) if leg else None
        if lot is None:
            return None
        if quantity is not None:
            lot.quantity = max(0, quantity)
        if price is not None:
            lot.price = max(0, price)
        if sold is not None and sold != lot.sold:
            lot.sold = sold
            lot.sold_at = time.time() if sold else None
        self.save()
        return lot

    def remove_set_lot(self, set_id: int, item_id: int, lot_id: str) -> bool:
        position = self._sets.get(set_id)
        leg = position.sell_leg(item_id) if position else None
        if leg is None or leg.lot(lot_id) is None:
            return False
        leg.lots = [lot for lot in leg.lots if lot.lot_id != lot_id]
        self.save()
        return True

    # --- magic trades --------------------------------------------------------
    # Keyed by "spell:input id", so the same item can be pinned for two spells.

    def all_magic(self) -> list[MagicPosition]:
        """Pinned spells, most recently pinned first."""
        return sorted(self._magic.values(), key=lambda p: p.pinned_at, reverse=True)

    def get_magic(self, key: str) -> MagicPosition | None:
        return self._magic.get(key)

    def pin_magic(
        self,
        spell: str,
        input_id: int,
        output_id: int | None,
        staff: bool,
        rune_cost: int | None,
    ) -> MagicPosition:
        """Pin a spell on an item. Pinning one already pinned leaves it untouched."""
        key = magic_key(spell, input_id)
        existing = self._magic.get(key)
        if existing is not None:
            return existing
        position = MagicPosition(
            spell=spell, input_id=input_id, output_id=output_id,
            staff=staff, rune_cost=rune_cost,
        )
        self._magic[key] = position
        self.save()
        return position

    def remove_magic(self, key: str) -> bool:
        if self._magic.pop(key, None) is None:
            return False
        self.save()
        return True

    def update_magic(
        self,
        key: str,
        buy_price: int | None = None,
        buy_quantity: int | None = None,
        bought: int | None = None,
        cast: int | None = None,
        rune_cost: int | None = None,
        reference_high: int | None = None,
    ) -> MagicPosition | None:
        """Change the trade. Only the fields passed are touched.

        `reference_high` is only stored if there is no snapshot yet.
        """
        position = self._magic.get(key)
        if position is None:
            return None
        if buy_price is not None:
            position.buy_price = max(0, buy_price)
        if buy_quantity is not None:
            position.buy_quantity = max(0, buy_quantity)
        if bought is not None:
            position.bought = max(0, bought)
        if cast is not None:
            position.cast = max(0, cast)
        if rune_cost is not None:
            position.rune_cost = max(0, rune_cost)
        if reference_high is not None and position.reference_high is None:
            position.reference_high = reference_high
        self.save()
        return position

    def add_magic_lot(self, key: str, quantity: int, price: int) -> SellLot | None:
        position = self._magic.get(key)
        if position is None or position.output_id is None:
            return None
        lot = SellLot(lot_id=new_lot_id(), quantity=max(0, quantity), price=max(0, price))
        position.lots.append(lot)
        self.save()
        return lot

    def update_magic_lot(
        self,
        key: str,
        lot_id: str,
        quantity: int | None = None,
        price: int | None = None,
        sold: bool | None = None,
    ) -> SellLot | None:
        position = self._magic.get(key)
        lot = position.lot(lot_id) if position else None
        if lot is None:
            return None
        _change_lot(lot, quantity, price, sold)
        self.save()
        return lot

    def remove_magic_lot(self, key: str, lot_id: str) -> bool:
        position = self._magic.get(key)
        if position is None or position.lot(lot_id) is None:
            return False
        position.lots = [lot for lot in position.lots if lot.lot_id != lot_id]
        self.save()
        return True

    # --- disk ----------------------------------------------------------------

    def load(self) -> None:
        self._positions = {}
        self._sets = {}
        self._magic = {}
        self.load_error = None
        if not self.path.exists():
            return
        try:
            raw = json.loads(self.path.read_text("utf-8"))
            for entry in raw.get("positions", []):
                position = Position.from_dict(entry)
                self._positions[position.item_id] = position
            # Version 2 files have no set trades, which reads as none.
            # A bad set entry is skipped on its own, so it cannot take the
            # item trades down with it.
            for entry in raw.get("set_positions", []):
                try:
                    set_position = SetPosition.from_dict(entry)
                except (ValueError, KeyError, TypeError) as exc:
                    self.load_error = f"skipped an unreadable set trade in {self.path}: {exc}"
                    log.warning("%s", self.load_error)
                    continue
                self._sets[set_position.set_id] = set_position
            # Version 3 and earlier have no magic trades.
            for entry in raw.get("magic_positions", []):
                try:
                    magic_position = MagicPosition.from_dict(entry)
                except (ValueError, KeyError, TypeError) as exc:
                    self.load_error = f"skipped an unreadable magic trade in {self.path}: {exc}"
                    log.warning("%s", self.load_error)
                    continue
                self._magic[magic_position.key] = magic_position
            log.info(
                "loaded %d pinned items, %d set trades and %d magic trades from %s",
                len(self._positions), len(self._sets), len(self._magic), self.path,
            )
        except (OSError, ValueError, KeyError, TypeError) as exc:
            # Never let a bad file stop the app starting.
            self.load_error = f"could not read {self.path}: {exc}"
            log.warning("%s", self.load_error)

    def save(self) -> None:
        payload = {
            "version": STORE_VERSION,
            "positions": [p.to_dict() for p in self.all()],
            "set_positions": [p.to_dict() for p in self.all_sets()],
            "magic_positions": [p.to_dict() for p in self.all_magic()],
        }
        try:
            self.path.parent.mkdir(parents=True, exist_ok=True)
            # Write beside the target, then rename, so a crash mid-write cannot
            # truncate the real file.
            handle, temp_name = tempfile.mkstemp(
                dir=self.path.parent, prefix=".positions-", suffix=".tmp"
            )
            with os.fdopen(handle, "w", encoding="utf-8") as fh:
                json.dump(payload, fh, indent=2)
            os.replace(temp_name, self.path)
        except OSError as exc:
            log.warning("could not save pinned items to %s: %s", self.path, exc)
