"""FastAPI app: a few JSON endpoints plus the static single-page UI.

Design choice: /api/flips returns every item with its numbers already
computed. Filtering and sorting then happen in the browser, so moving a slider
is instant and never touches the wiki. The payload is roughly 1-2 MB, which is
fine over localhost.
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict
from pathlib import Path
from typing import Any

from fastapi import Body, FastAPI, HTTPException, Query
from fastapi.middleware.gzip import GZipMiddleware
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from .cache import PriceCache
from .config import DEFAULT_POSITIONS_FILE, LEGACY_POSITIONS_FILE, Settings
from .flips import (
    analyse_trend,
    assess_long_term,
    expected_drift,
    plan_flip,
    summarise_trade,
)
from .magic import (
    HIGH_ALCH,
    LOW_ALCH,
    build_magic_opportunities,
    find_recipe,
    rune_cost,
    summarise_magic_trade,
)
from .positions import PositionStore, adopt_legacy_file, magic_key
from .sets import PACK, UNPACK, build_set_flips, set_legs, summarise_set_trade
from .wiki_api import VALID_TIMESTEPS, WikiApiError, WikiPricesClient

log = logging.getLogger(__name__)

STATIC_DIR = Path(__file__).parent / "static"


def create_app(settings: Settings) -> FastAPI:
    """Build the app. The cache lives on app.state for the request handlers."""

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        client = WikiPricesClient(user_agent=settings.user_agent)
        cache = PriceCache(client, settings)
        app.state.cache = cache
        app.state.settings = settings
        if settings.positions_file == DEFAULT_POSITIONS_FILE:
            adopt_legacy_file(settings.positions_file, LEGACY_POSITIONS_FILE)
        app.state.positions = PositionStore(settings.positions_file)
        log.info("User-Agent: %s", settings.user_agent)
        await cache.start()
        try:
            yield
        finally:
            await cache.stop()
            await client.aclose()

    app = FastAPI(title="OSRS Finance Hub", lifespan=lifespan, docs_url="/api/docs")

    # The scanner payload is a few megabytes of JSON and the page refetches it
    # every minute. Compressed it is under a tenth of that, which is the
    # difference between usable and not on a phone.
    app.add_middleware(GZipMiddleware, minimum_size=1024)

    @app.get("/api/status")
    async def status() -> dict[str, Any]:
        """Freshness and config, for the header banner."""
        return app.state.cache.status()

    @app.get("/api/flips")
    async def flips() -> dict[str, Any]:
        """Every item with computed flip numbers, best estimated profit first."""
        cache: PriceCache = app.state.cache
        return {
            "status": cache.status(),
            "items": [flip.to_dict() for flip in cache.build_flips()],
        }

    @app.get("/api/sets")
    async def sets() -> dict[str, Any]:
        """Every item set, priced against its pieces both ways round."""
        cache: PriceCache = app.state.cache
        settings: Settings = app.state.settings
        by_id = {flip.id: flip for flip in cache.build_flips()}
        return {
            "sets": [s.to_dict() for s in build_set_flips(by_id, settings.volume_fraction)],
        }

    @app.get("/api/magic")
    async def magic() -> dict[str, Any]:
        """Alchemy that pays and every enchant, priced from the cached book."""
        cache: PriceCache = app.state.cache
        settings: Settings = app.state.settings
        by_id = {flip.id: flip for flip in cache.build_flips()}
        rows = build_magic_opportunities(by_id, settings.volume_fraction)
        return {"opportunities": [r.to_dict() for r in rows]}

    @app.get("/api/item/{item_id}")
    async def item(item_id: int) -> dict[str, Any]:
        """One item's numbers, for the detail view."""
        cache: PriceCache = app.state.cache
        for flip in cache.build_flips():
            if flip.id == item_id:
                return flip.to_dict()
        raise HTTPException(status_code=404, detail=f"unknown item id {item_id}")

    @app.get("/api/plan/{item_id}")
    async def plan(
        item_id: int,
        buy: int = Query(..., ge=0),
        sell: int = Query(..., ge=0),
        quantity: int = Query(..., ge=0),
    ) -> dict[str, Any]:
        """Profit and speed for one hypothetical trade.

        The arithmetic runs here rather than in the browser so the tax rules
        have exactly one home, in flips.py.
        """
        cache: PriceCache = app.state.cache
        settings: Settings = app.state.settings
        flip = next((f for f in cache.build_flips() if f.id == item_id), None)
        if flip is None:
            raise HTTPException(status_code=404, detail=f"unknown item id {item_id}")

        result = plan_flip(
            buy=buy,
            sell=sell,
            quantity=quantity,
            low=flip.low,
            high=flip.high,
            hour_low_volume=flip.hour_low_volume,
            hour_high_volume=flip.hour_high_volume,
            buy_limit=flip.limit,
            volume_fraction=settings.volume_fraction,
            suspicious_spread=flip.suspicious_spread,
            price_age_seconds=flip.price_age_seconds,
        )
        return result.to_dict()

    # --- pinned items and open positions ------------------------------------

    def _position_payload(position, flip) -> dict[str, Any]:
        """One pinned item, merged with whatever the live data says about it."""
        entry: dict[str, Any] = {
            "position": position.to_dict(),
            "item": flip.to_dict() if flip else None,
            "trade": None,
        }
        if position.is_open_position:
            entry["trade"] = summarise_trade(
                buy_price=position.buy_price,
                buy_quantity=position.buy_quantity,
                bought=position.bought,
                lots=[lot.to_dict() for lot in position.lots],
                low=flip.low if flip else None,
                high=flip.high if flip else None,
                reference_sell=position.reference_sell,
            ).to_dict()
        return entry

    def _flip_or_404(item_id: int):
        cache: PriceCache = app.state.cache
        flip = next((f for f in cache.build_flips() if f.id == item_id), None)
        if flip is None:
            raise HTTPException(status_code=404, detail=f"unknown item id {item_id}")
        return flip

    def _whole_number(body: dict[str, Any], key: str) -> int | None:
        if body.get(key) is None:
            return None
        try:
            return int(body[key])
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail=f"{key} must be a number") from None

    # --- pinned set trades ---------------------------------------------------

    def _brief(flip) -> dict[str, Any] | None:
        """The few live fields a set trade card shows for each item."""
        if flip is None:
            return None
        return {
            "id": flip.id, "name": flip.name, "icon": flip.icon,
            "low": flip.low, "high": flip.high, "limit": flip.limit,
            "price_age_seconds": flip.price_age_seconds,
        }

    def _set_payload(position, by_id: dict[int, Any]) -> dict[str, Any]:
        """One pinned set trade, merged with the live book."""
        ids = [position.set_id] + [leg.item_id for leg in position.buys + position.sells]
        summary = summarise_set_trade(
            direction=position.direction,
            buys=[asdict(leg) for leg in position.buys],
            sells=[{"item_id": leg.item_id, "lots": [lot.to_dict() for lot in leg.lots]}
                   for leg in position.sells],
            prices={i: (by_id[i].low, by_id[i].high) for i in ids if i in by_id},
            reference_highs=position.reference_highs,
        )
        return {
            "position": position.to_dict(),
            "items": {str(i): _brief(by_id.get(i)) for i in dict.fromkeys(ids)},
            "trade": summary.to_dict(),
        }

    def _set_or_404(set_id: int):
        position = app.state.positions.get_set(set_id)
        if position is None:
            raise HTTPException(status_code=404, detail="that set trade is not pinned")
        return position

    def _flips_by_id() -> dict[int, Any]:
        return {f.id: f for f in app.state.cache.build_flips()}

    @app.put("/api/set-positions/{set_id}")
    async def pin_set(
        set_id: int,
        body: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        """Pin a set trade in one direction. Pinning it again changes nothing."""
        direction = body.get("direction")
        if direction not in (PACK, UNPACK):
            raise HTTPException(status_code=422, detail="direction must be pack or unpack")
        legs = set_legs(set_id, direction)
        if legs is None:
            raise HTTPException(status_code=404, detail=f"unknown set id {set_id}")
        store: PositionStore = app.state.positions
        existing = store.get_set(set_id)
        if existing is not None and existing.direction != direction:
            raise HTTPException(
                status_code=409,
                detail=f"this set is already pinned to {existing.direction}; unpin it first",
            )
        position = store.pin_set(set_id, direction, *legs)
        return _set_payload(position, _flips_by_id())

    @app.delete("/api/set-positions/{set_id}")
    async def unpin_set(set_id: int) -> dict[str, Any]:
        store: PositionStore = app.state.positions
        return {"removed": store.remove_set(set_id), "id": set_id}

    @app.put("/api/set-positions/{set_id}/buys/{item_id}")
    async def update_set_buy(
        set_id: int,
        item_id: int,
        body: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        """Record what you offered for one item, how many, and what arrived.

        The first buy price recorded on the trade snapshots every item's
        current instant-buy price, the baseline the advice measures against.
        """
        position = _set_or_404(set_id)
        store: PositionStore = app.state.positions
        by_id = _flips_by_id()

        buy_price = _whole_number(body, "buy_price")
        snapshot = None
        if buy_price is not None and not position.reference_highs:
            ids = [leg.item_id for leg in position.buys + position.sells]
            snapshot = {i: by_id[i].high for i in ids if i in by_id and by_id[i].high is not None}

        leg = store.update_set_buy(
            set_id, item_id,
            buy_price=buy_price,
            buy_quantity=_whole_number(body, "buy_quantity"),
            bought=_whole_number(body, "bought"),
            reference_highs=snapshot,
        )
        if leg is None:
            raise HTTPException(status_code=404, detail="that item is not bought in this trade")
        return _set_payload(position, by_id)

    @app.post("/api/set-positions/{set_id}/sells/{item_id}/lots")
    async def add_set_lot(
        set_id: int,
        item_id: int,
        body: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        position = _set_or_404(set_id)
        store: PositionStore = app.state.positions
        quantity = _whole_number(body, "quantity")
        price = _whole_number(body, "price")
        if quantity is None or price is None:
            raise HTTPException(status_code=422, detail="a lot needs both a quantity and a price")
        if quantity <= 0:
            raise HTTPException(status_code=422, detail="quantity must be above zero")
        if store.add_set_lot(set_id, item_id, quantity, price) is None:
            raise HTTPException(status_code=404, detail="that item is not sold in this trade")
        return _set_payload(position, _flips_by_id())

    @app.put("/api/set-positions/{set_id}/sells/{item_id}/lots/{lot_id}")
    async def update_set_lot(
        set_id: int,
        item_id: int,
        lot_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        position = _set_or_404(set_id)
        store: PositionStore = app.state.positions
        sold = body.get("sold")
        updated = store.update_set_lot(
            set_id, item_id, lot_id,
            quantity=_whole_number(body, "quantity"),
            price=_whole_number(body, "price"),
            sold=(bool(sold) if sold is not None else None),
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="no such lot")
        return _set_payload(position, _flips_by_id())

    @app.delete("/api/set-positions/{set_id}/sells/{item_id}/lots/{lot_id}")
    async def delete_set_lot(set_id: int, item_id: int, lot_id: str) -> dict[str, Any]:
        position = _set_or_404(set_id)
        store: PositionStore = app.state.positions
        if not store.remove_set_lot(set_id, item_id, lot_id):
            raise HTTPException(status_code=404, detail="no such lot")
        return _set_payload(position, _flips_by_id())

    # --- pinned magic trades -------------------------------------------------

    def _magic_payload(position, by_id: dict[int, Any]) -> dict[str, Any]:
        """One pinned spell, merged with the live book."""
        recipe = find_recipe(position.spell, position.input_id)
        kind, spell = (recipe[0], recipe[1]) if recipe else (None, None)
        item = by_id.get(position.input_id)
        output = by_id.get(position.output_id) if position.output_id is not None else None
        alch_value = None
        if item is not None:
            alch_value = (item.highalch if kind == HIGH_ALCH
                          else item.lowalch if kind == LOW_ALCH else None)
        trade = summarise_magic_trade(
            kind=kind or "",
            buy_price=position.buy_price,
            buy_quantity=position.buy_quantity,
            bought=position.bought,
            cast=position.cast,
            rune_cost=position.rune_cost,
            lots=[lot.to_dict() for lot in position.lots],
            alch_value=alch_value,
            output_low=output.low if output else None,
            output_high=output.high if output else None,
            reference_high=position.reference_high,
        )
        return {
            "position": position.to_dict(),
            "kind": kind,
            "spell_name": spell.name if spell else position.spell,
            "level": spell.level if spell else None,
            "alch_value": alch_value,
            "item": _brief(item),
            "output": _brief(output),
            "trade": trade.to_dict(),
        }

    def _magic_or_404(spell: str, input_id: int):
        position = app.state.positions.get_magic(magic_key(spell, input_id))
        if position is None:
            raise HTTPException(status_code=404, detail="that spell is not pinned on this item")
        return position

    @app.put("/api/magic-positions/{spell}/{input_id}")
    async def pin_magic(
        spell: str,
        input_id: int,
        body: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        """Pin a spell on an item. The runes are priced now, with or without
        the elemental runes as `staff` says; you can correct them later."""
        recipe = find_recipe(spell, input_id)
        by_id = _flips_by_id()
        if recipe is None or input_id not in by_id:
            raise HTTPException(status_code=404, detail="no such spell on that item")
        kind, spell_def, output_id = recipe
        staff = bool(body.get("staff", True))
        runes = rune_cost(spell_def, by_id)
        position = app.state.positions.pin_magic(
            spell, input_id, output_id, staff,
            runes.patient_staff if staff else runes.patient,
        )
        return _magic_payload(position, by_id)

    @app.delete("/api/magic-positions/{spell}/{input_id}")
    async def unpin_magic(spell: str, input_id: int) -> dict[str, Any]:
        store: PositionStore = app.state.positions
        return {"removed": store.remove_magic(magic_key(spell, input_id))}

    @app.put("/api/magic-positions/{spell}/{input_id}/trade")
    async def update_magic(
        spell: str,
        input_id: int,
        body: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        """Record the buy offer, what arrived, how many are cast, or the runes.

        The first buy price snapshots the enchanted item's instant-buy price,
        the baseline the advice measures against.
        """
        position = _magic_or_404(spell, input_id)
        by_id = _flips_by_id()
        buy_price = _whole_number(body, "buy_price")
        reference = None
        if buy_price is not None and position.output_id in by_id:
            reference = by_id[position.output_id].high
        app.state.positions.update_magic(
            position.key,
            buy_price=buy_price,
            buy_quantity=_whole_number(body, "buy_quantity"),
            bought=_whole_number(body, "bought"),
            cast=_whole_number(body, "cast"),
            rune_cost=_whole_number(body, "rune_cost"),
            reference_high=reference,
        )
        return _magic_payload(position, by_id)

    @app.post("/api/magic-positions/{spell}/{input_id}/lots")
    async def add_magic_lot(
        spell: str,
        input_id: int,
        body: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        position = _magic_or_404(spell, input_id)
        quantity = _whole_number(body, "quantity")
        price = _whole_number(body, "price")
        if quantity is None or price is None:
            raise HTTPException(status_code=422, detail="a lot needs both a quantity and a price")
        if quantity <= 0:
            raise HTTPException(status_code=422, detail="quantity must be above zero")
        if app.state.positions.add_magic_lot(position.key, quantity, price) is None:
            raise HTTPException(status_code=404, detail="alchemy pays in coins; there is nothing to sell")
        return _magic_payload(position, _flips_by_id())

    @app.put("/api/magic-positions/{spell}/{input_id}/lots/{lot_id}")
    async def update_magic_lot(
        spell: str,
        input_id: int,
        lot_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        position = _magic_or_404(spell, input_id)
        sold = body.get("sold")
        updated = app.state.positions.update_magic_lot(
            position.key, lot_id,
            quantity=_whole_number(body, "quantity"),
            price=_whole_number(body, "price"),
            sold=(bool(sold) if sold is not None else None),
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="no such lot")
        return _magic_payload(position, _flips_by_id())

    @app.delete("/api/magic-positions/{spell}/{input_id}/lots/{lot_id}")
    async def delete_magic_lot(spell: str, input_id: int, lot_id: str) -> dict[str, Any]:
        position = _magic_or_404(spell, input_id)
        if not app.state.positions.remove_magic_lot(position.key, lot_id):
            raise HTTPException(status_code=404, detail="no such lot")
        return _magic_payload(position, _flips_by_id())

    @app.get("/api/positions")
    async def list_positions() -> dict[str, Any]:
        """Everything pinned, priced against the current book."""
        store: PositionStore = app.state.positions
        cache: PriceCache = app.state.cache
        by_id = {f.id: f for f in cache.build_flips()}
        return {
            "positions": [
                _position_payload(p, by_id.get(p.item_id)) for p in store.all()
            ],
            "set_positions": [_set_payload(p, by_id) for p in store.all_sets()],
            "magic_positions": [_magic_payload(p, by_id) for p in store.all_magic()],
            "store_error": store.load_error,
            "file": str(store.path),
        }

    @app.put("/api/positions/{item_id}")
    async def upsert_position(
        item_id: int,
        body: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        """Pin an item, or change the trade on it.

        Only the fields present in the body are changed. The first time a buy
        price is recorded we also snapshot what the market was paying, so the
        advice can later say how far the margin has moved.
        """
        flip = _flip_or_404(item_id)
        store: PositionStore = app.state.positions

        buy_price = _whole_number(body, "buy_price")
        reference = _whole_number(body, "reference_sell")

        existing = store.get(item_id)
        if (
            reference is None
            and buy_price is not None
            and (existing is None or existing.reference_sell is None)
        ):
            reference = flip.high

        position = store.upsert(
            item_id,
            buy_price=buy_price,
            buy_quantity=_whole_number(body, "buy_quantity"),
            bought=_whole_number(body, "bought"),
            reference_sell=reference,
            note=body.get("note"),
            clear_buy=bool(body.get("clear_buy")),
        )
        return _position_payload(position, flip)

    @app.delete("/api/positions/{item_id}")
    async def delete_position(item_id: int) -> dict[str, Any]:
        """Unpin an item. Unpinning something not pinned is not an error."""
        store: PositionStore = app.state.positions
        return {"removed": store.remove(item_id), "id": item_id}

    @app.post("/api/positions/{item_id}/lots")
    async def add_lot(
        item_id: int,
        body: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        """List a parcel of stock for sale at a given price."""
        flip = _flip_or_404(item_id)
        store: PositionStore = app.state.positions

        quantity = _whole_number(body, "quantity")
        price = _whole_number(body, "price")
        if quantity is None or price is None:
            raise HTTPException(
                status_code=422, detail="a lot needs both a quantity and a price"
            )
        if quantity <= 0:
            raise HTTPException(status_code=422, detail="quantity must be above zero")

        if store.add_lot(item_id, quantity=quantity, price=price) is None:
            raise HTTPException(status_code=404, detail="that item is not pinned")
        return _position_payload(store.get(item_id), flip)

    @app.put("/api/positions/{item_id}/lots/{lot_id}")
    async def update_lot(
        item_id: int,
        lot_id: str,
        body: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        """Change a lot, most often to mark it sold."""
        flip = _flip_or_404(item_id)
        store: PositionStore = app.state.positions

        sold = body.get("sold")
        updated = store.update_lot(
            item_id,
            lot_id,
            quantity=_whole_number(body, "quantity"),
            price=_whole_number(body, "price"),
            sold=(bool(sold) if sold is not None else None),
        )
        if updated is None:
            raise HTTPException(status_code=404, detail="no such lot")
        return _position_payload(store.get(item_id), flip)

    @app.delete("/api/positions/{item_id}/lots/{lot_id}")
    async def delete_lot(item_id: int, lot_id: str) -> dict[str, Any]:
        """Cancel a listing."""
        flip = _flip_or_404(item_id)
        store: PositionStore = app.state.positions
        if not store.remove_lot(item_id, lot_id):
            raise HTTPException(status_code=404, detail="no such lot")
        return _position_payload(store.get(item_id), flip)

    @app.get("/api/trend/{item_id}")
    async def trend(item_id: int) -> dict[str, Any]:
        """Price direction and safety rating for one item.

        Reads the same cached timeseries the chart uses, so opening an item
        and rating it costs at most one extra request, and none at all once
        the series is cached.
        """
        cache: PriceCache = app.state.cache
        settings: Settings = app.state.settings
        flip = next((f for f in cache.build_flips() if f.id == item_id), None)
        if flip is None:
            raise HTTPException(status_code=404, detail=f"unknown item id {item_id}")

        try:
            points = await cache.get_timeseries(item_id, settings.trend_timestep)
        except WikiApiError as exc:
            raise HTTPException(status_code=502, detail=str(exc)) from exc

        analysis = analyse_trend(points, settings.trend_timestep)
        if analysis is None:
            return {"id": item_id, "trend": None,
                    "reason": "not enough price history to judge"}

        # What the trend alone would do to the price over a typical hold.
        drift = expected_drift(analysis, flip.low or 0, flip.est_hours_total)
        drift_vs_margin = None
        if drift is not None and flip.margin and flip.margin > 0:
            drift_vs_margin = -drift / flip.margin

        return {
            "id": item_id,
            "trend": analysis.to_dict(),
            "expected_drift_per_unit": drift,
            "drift_as_share_of_margin": drift_vs_margin,
        }

    @app.post("/api/long-term")
    async def long_term(
        body: dict[str, Any] = Body(default_factory=dict),
    ) -> dict[str, Any]:
        """Rate a batch of candidate items as buy-and-hold prospects.

        Trend history has no bulk endpoint, so the caller sends a shortlist it
        has already narrowed down with the data we do have in bulk. The batch
        is capped and fetched a few at a time, which keeps the request rate
        civil however the UI is driven. Anything already cached costs nothing.
        """
        cache: PriceCache = app.state.cache
        settings: Settings = app.state.settings

        raw_ids = body.get("ids") or []
        if not isinstance(raw_ids, list):
            raise HTTPException(status_code=422, detail="ids must be a list")
        try:
            ids = [int(i) for i in raw_ids][: settings.trend_batch_size]
        except (TypeError, ValueError):
            raise HTTPException(status_code=422, detail="ids must be numbers") from None

        semaphore = asyncio.Semaphore(3)

        async def rate(item_id: int) -> dict[str, Any]:
            async with semaphore:
                try:
                    points = await cache.get_timeseries(item_id, settings.trend_timestep)
                except WikiApiError as exc:
                    return {"id": item_id, "error": str(exc)}
            trend = analyse_trend(points, settings.trend_timestep)
            view = assess_long_term(trend)
            return {
                "id": item_id,
                "trend": trend.to_dict() if trend else None,
                "long_term": view.to_dict() if view else None,
            }

        results = await asyncio.gather(*(rate(i) for i in ids))
        return {"rated": results, "batch_size": settings.trend_batch_size}

    @app.get("/api/timeseries/{item_id}")
    async def timeseries(
        item_id: int,
        timestep: str = Query("1h", pattern="^(5m|1h|6h|24h)$"),
    ) -> dict[str, Any]:
        """Price history for the chart. Cached briefly per item and timestep."""
        if timestep not in VALID_TIMESTEPS:
            raise HTTPException(status_code=400, detail="bad timestep")
        cache: PriceCache = app.state.cache
        try:
            points = await cache.get_timeseries(item_id, timestep)
        except WikiApiError as exc:
            # Surface it as a 502 so the UI can say "chart unavailable" without
            # losing the rest of the detail view.
            raise HTTPException(status_code=502, detail=str(exc)) from exc
        return {"id": item_id, "timestep": timestep, "points": points}

    @app.exception_handler(WikiApiError)
    async def wiki_error_handler(_request, exc: WikiApiError) -> JSONResponse:
        return JSONResponse(status_code=502, content={"detail": str(exc)})

    @app.middleware("http")
    async def always_revalidate_the_ui(request, call_next):
        """Make the browser check whether the page and script have changed.

        Without a Cache-Control header browsers fall back to guessing, and
        happily serve a stale script for hours. That produces the worst kind
        of bug: a new page wired to an old script, where the markup is there
        but nothing responds. "no-cache" does not mean "do not store", it
        means "ask first", and the ETag already present makes that answer a
        304 with no body when nothing has moved.
        """
        response = await call_next(request)
        path = request.url.path
        if path == "/" or path.startswith("/static/"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    # Static UI. Mounted last so /api/* wins.
    app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")

    @app.get("/")
    async def index() -> FileResponse:
        return FileResponse(STATIC_DIR / "index.html")

    return app
