# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OSRS Flip Finder: a local FastAPI web app that reads the OSRS Wiki real-time prices API (`prices.runescape.wiki`) and ranks Grand Exchange flips. No database, no build step for the frontend. `README.md` is the detailed spec for the maths, UI behaviour and thresholds; read the relevant section before changing a feature.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                     # app + pytest, pytest-asyncio

flipfinder serve --contact "you@example.com" --no-browser   # http://127.0.0.1:8080/
flipfinder serve --verbose                  # debug logging; API docs at /api/docs

pytest                                      # whole suite (asyncio_mode = auto)
pytest tests/test_flips.py                  # one file
pytest tests/test_flips.py -k ge_tax        # tests matching a name
```

No linter or formatter is configured.

## Architecture

Data flows one way: wiki API → `PriceCache` (memory) → `flips.py` (pure maths) → `server.py` JSON → `static/app.js`, which does all filtering, sorting and searching client-side.

- **`wiki_api.py`** – thin httpx client, one method per endpoint (`/mapping`, `/latest`, `/1h`, `/5m`, `/timeseries`). Raises `WikiApiError`.
- **`cache.py`** – `PriceCache` primes on startup, then background loops refresh `/latest` (60s) and `/1h`+`/5m` (300s). A failed refresh keeps the last good data and records the error in `EndpointState`; `/api/status` exposes this for the stale-data banner. `/timeseries` is fetched on demand and cached per `(item_id, timestep)` with a TTL scaled by `TIMESERIES_TTL_MULTIPLIER`.
- **`flips.py`** – all business logic as pure functions with no I/O: GE tax (`ge_tax` and three constants), `build_flip`/`build_all_flips`, suggested prices, fill-time estimates, trade planner (`plan_flip`), trend/safety rating (`analyse_trend`), break-even search, live-trade valuation (`summarise_trade`, `advise_on_buying`), profit per hour, long-term dip test. Thresholds are named module-level constants grouped by section. Most tests live here.
- **`sets.py` / `set_data.py`** – item set arbitrage (Sets tab, `/api/sets`). `build_set_flips` takes the per-item `Flip`s keyed by id and prices each set against its pieces both ways: unpack (buy set, sell pieces) and pack (buy pieces, sell set). `set_data.ITEM_SETS` is a static table of set → component ids taken from the wiki, because the prices API has no set data; add new sets there. `summarise_set_trade` values a pinned set trade (complete sets = the scarcest piece bought; unpack shares the set cost across pieces by their snapshotted prices).
- **`magic.py` / `magic_data.py`** – alchemy and enchanting (Magic tab, `/api/magic`). `build_magic_opportunities` prices each spell on each item per item, with rune costs both with and without elemental runes (`_staff` fields); the frontend picks one. Units per 4h are capped by buy limit, volume and casting speed. Alchemy rows are pre-filtered to those that pay; every enchant is returned. `magic_data` is static wiki data (spells, rune ids, enchant recipes). `summarise_magic_trade` values a pinned spell (cost each = buy price + snapshotted runes; alch coins are banked as you cast, enchanted items sell in lots); `find_recipe` validates a spell/item pair.
- **`positions.py`** – `PositionStore`: pinned items, their buy offers and sell lots, plus pinned set trades (`SetPosition`: buy legs and sell legs with lots, keyed by set id, routes under `/api/set-positions`), plus pinned spells (`MagicPosition`, keyed by `"spell:input_id"`, routes under `/api/magic-positions/{spell}/{input_id}`), persisted to `~/.flipfinder/positions.json` via write-temp-then-rename. Loading upgrades the older single-quantity format and treats a corrupt file as empty.
- **`server.py`** – `create_app(settings)`; cache, settings and positions store live on `app.state`, created in the lifespan. Flips are recomputed from the cache per request (`cache.build_flips()`). GZip middleware (the `/api/flips` payload is ~4 MB), `Cache-Control: no-cache` on `/` and `/static/*`, static mount registered last so `/api/*` wins.
- **`config.py`** – `Settings` dataclass; defaults ← `FLIPFINDER_*` env vars (`from_env`) ← CLI flags (`cli.py`). Add a new setting in all three places and in the README tables.
- **`static/`** – vanilla JS single page (`index.html`, `app.js`, `style.css`); Chart.js from a CDN. Six views (home / scanner / quick / long / sets / magic) selected via the URL hash; home is the default and summarises the others by calling each tab's own filter function (`passesFilters`, `passesQuick`, `setRows`, `magicRows`), so keep those reusable. The scanner wires every `.filters` input and `thead th[data-sort]` globally, so other tables use their own sort attribute (the Sets table uses `data-set-sort`). `SAFETY_RANK` in `app.js` must match `SAFETY_ORDER` in `flips.py`.

## Constraints to preserve

- **API etiquette.** The wiki blocks misbehaving clients. Always send the descriptive User-Agent (`Settings.user_agent`). Page loads, filters and sorting must never trigger a wiki request; only the background loops do. Per-item `/timeseries` calls happen only on explicit user action (opening an item, "Rate safety", long-term scan), capped at `trend_batch_size` and limited to 3 concurrent via a semaphore. Never add loops that fetch per-item data across all items.
- **Price naming.** In the API, `high` is the instant-buy price and `low` is the instant-sell price. Flippers buy near `low` and sell near `high`. Your buy offer fills from instant-sell volume, your sell offer from instant-buy volume.
- **Aggression is measured against the post-tax margin**, not the raw spread (see README "Suggested prices").
- Unrated items are "unknown", not "safe"; filters must not treat them as safe.
- `app.js` holds back re-rendering live-trade cards while the user is typing in one (`isEditingInside`, `pendingPinnedRender`); keep this when changing the refresh path.
