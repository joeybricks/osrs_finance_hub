# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

OSRS Finance Hub: a local FastAPI web app that reads the OSRS Wiki real-time prices API (`prices.runescape.wiki`) and ranks Grand Exchange flips. No database, no build step for the frontend. `README.md` is the detailed spec for the maths, UI behaviour and thresholds; read the relevant section before changing a feature.

## Plan 

OSRS Grand Exchange flipping app, grown into a low-cost, multi-cloud learning project: price and text ingestion, LLM signal extraction (Gemma), evals, scoring/backtesting, and an agent layer. The aim is cloud solution-design experience as much as the app itself.

## Commands

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"                     # app + pytest, pytest-asyncio

osrs_finance_hub serve --contact "you@example.com" --no-browser   # http://127.0.0.1:8080/
osrs_finance_hub serve --verbose                  # debug logging; API docs at /api/docs

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
- **`positions.py`** – `PositionStore`: pinned items, their buy offers and sell lots, plus pinned set trades (`SetPosition`: buy legs and sell legs with lots, keyed by set id, routes under `/api/set-positions`), plus pinned spells (`MagicPosition`, keyed by `"spell:input_id"`, routes under `/api/magic-positions/{spell}/{input_id}`), persisted to `~/.osrs_finance_hub/positions.json` via write-temp-then-rename (copied once from the pre-rename `~/.flipfinder/` on first start). Loading upgrades the older single-quantity format and treats a corrupt file as empty.
- **`server.py`** – `create_app(settings)`; cache, settings and positions store live on `app.state`, created in the lifespan. Flips are recomputed from the cache per request (`cache.build_flips()`). GZip middleware (the `/api/flips` payload is ~4 MB), `Cache-Control: no-cache` on `/` and `/static/*`, static mount registered last so `/api/*` wins.
- **`config.py`** – `Settings` dataclass; defaults ← `OSRS_FINANCE_HUB_*` env vars (`from_env`) ← CLI flags (`cli.py`). Add a new setting in all three places and in the README tables.
- **`static/`** – vanilla JS single page (`index.html`, `app.js`, `style.css`); Chart.js from a CDN. Six views (home / scanner / quick / long / sets / magic) selected via the URL hash; home is the default and summarises the others by calling each tab's own filter function (`passesFilters`, `passesQuick`, `setRows`, `magicRows`), so keep those reusable. The scanner wires every `.filters` input and `thead th[data-sort]` globally, so other tables use their own sort attribute (the Sets table uses `data-set-sort`). `SAFETY_RANK` in `app.js` must match `SAFETY_ORDER` in `flips.py`.

## Constraints to preserve

- **API etiquette.** The wiki blocks misbehaving clients. Always send the descriptive User-Agent (`Settings.user_agent`). Page loads, filters and sorting must never trigger a wiki request; only the background loops do. Per-item `/timeseries` calls happen only on explicit user action (opening an item, "Rate safety", long-term scan), capped at `trend_batch_size` and limited to 3 concurrent via a semaphore. Never add loops that fetch per-item data across all items.
- **Price naming.** In the API, `high` is the instant-buy price and `low` is the instant-sell price. Flippers buy near `low` and sell near `high`. Your buy offer fills from instant-sell volume, your sell offer from instant-buy volume.
- **Aggression is measured against the post-tax margin**, not the raw spread (see README "Suggested prices").
- Unrated items are "unknown", not "safe"; filters must not treat them as safe.
- `app.js` holds back re-rendering live-trade cards while the user is typing in one (`isEditingInside`, `pendingPinnedRender`); keep this when changing the refresh path.


## Session protocol (important)
Work happens across many short sessions. `work_so_far/` is the memory between them.

**Start of session**
1. Read `work_so_far/status.md` (current epic, what's next, open issues).
2. Skim the most recent session notes in `work_so_far/`.
3. State in one or two lines where we are and what you propose to do. Do not re-plan finished work.

**End of session** (or when asked)
1. Write `work_so_far/YYYY-MM-DD-<topic>.md` with these sections:
   - **Done**: what changed and why
   - **Issues and fixes**: problems hit, root cause, how they were solved
   - **Moving forward**: open questions, trade-offs, next steps
   - **Learned**: the concept or gotcha worth remembering
2. Update `work_so_far/status.md` (keep it to about 20 lines).
3. Major decisions get an ADR, not just a session note.

Notes are written for Claude to resume from and for Joe to revise from. Keep them short, plain and specific.

## Layout
`app/` `src/` `infra/` (Terraform) `evals/` `ops/` `prompts/` (versioned) `work_so_far/`

## Hard rules
- **Cost**: budget is £2. Stay inside free tiers. `osrs-infra` has billing; `osrs-llm` has none. Never create billable resources outside Terraform. Flag anything that could cost money before doing it.
- **Secrets**: repo is public. No keys in code, config, logs or notes. Secrets go in Secret Manager. Auth to GCP via Workload Identity Federation only, no service account key files.
- **Infra changes** go through Terraform and CI, not click-ops. Run `terraform plan` and show the output before any apply.
- **Data and LLM**: deterministic entity linking comes before any LLM call. Prompts are versioned in `prompts/`. Changes to prompts or extraction need an eval run.
- Pin GitHub Actions to SHAs. Tests (pytest) and lint must pass before merge.

## Tickets and decisions
- Joe assigns work as tickets. Work through the assigned ticket; don't pick up other stories or epics unprompted.
- Claude may suggest structural changes (architecture, repo layout, service choices, sequencing). Raise them briefly with the reasoning, then wait.
- Joe's decision is final. If he declines a suggestion, proceed his way without relitigating, and record the decision in the session note.

## Working style
- Joe is an experienced data/ML engineer, but is learning cloud design here. Joe is self taught in many areas, so help him gain technical depth and terminologgy/service name understanding. Explain the *why* behind service choices and trade-offs, briefly.
- Prefer a diagram or pseudocode when introducing a new concept.
- Keep changes small and one epic story at a time. Ask before expanding scope.
- If something contradicts the epic plan or an ADR, say so rather than silently deviating.
- Commit sensibly and frequently: one logical change per commit with a clear message, but don't over-commit (no commits for trivial edits or half-finished work).

# Working with Claude on Jira Tickets

## Branching

- Always branch from `develop`.
- Branch name format: `<branch_type>-<ticket_number>-<description_of_task>`
  - Lowercase only.
  - Words in the description are separated by underscores.
  - Keep the description short; drop filler words like "and" or "the".

| Ticket | Ticket name | Branch name |
|--------|-------------|-------------|
| 004 | Creating SQL DB | `feature-004-creating_sql_db` |
| 504 | Removing Legacy Code and Clearing Old Tests | `chore-504-removing_legacy_code_clean_old_tests` |

### Multiple tickets on one branch

If Claude is asked to work on several tickets at once, join the ticket numbers with a hyphen:

`feature-004-504-short_description_of_combined_work`

## Pull Requests

Use this format for every PR.

```markdown
## Title
<Ticket number(s)> short summary of the change

## Purpose of PR
Why this change exists and which problem or ticket it addresses.

## What has been done
- Concise list of the changes made

## Future thoughts
- Follow-up work, known limitations, or ideas for later
```

## GitHub Actions guardrails

Private repo: 2,000 free min/month; at the $0 spending limit, jobs stop rather than bill. Runaway workflows are the main risk, so every workflow must follow these rules:

| Rule | Why |
|---|---|
| `timeout-minutes` on every job (≤10 unless justified) | Default is 6 h; one hung job can burn 360 min |
| `concurrency` with `cancel-in-progress: true` (false for `tf-apply`) | Rapid pushes only run the latest commit; applies queue, never abort |
| `paths:` filters on CI and evals | Skip runs when irrelevant files change |
| `@claude` triggers only on explicit mentions, with a job timeout | App tokens can trigger other workflows, so loops are possible |
| No new `schedule:` crons without approval | Frequent crons are the fastest way to exhaust minutes |
| Prefer `GITHUB_TOKEN` over PATs for commits | Commits made with it don't trigger further workflows |

Enforced by `ci/check_workflows.py` (fails PRs missing timeouts or adding unapproved crons). Usage is checked in the daily cost digest, with a GitHub budget alert at 50% and 80%.