"""Record a trimmed copy of the wiki prices API for the characterisation tests.

Run by hand, rarely (only when the recorded data needs replacing):

    uv run python app/tests/fixtures/record_wiki.py [--include 4151,11832]

Makes exactly four requests (/mapping, /latest, /1h, /5m) with the app's
User-Agent, keeps a chosen set of items and writes the raw responses to
app/tests/fixtures/wiki/. After re-recording, regenerate the snapshots:

    OSRS_UPDATE_SNAPSHOTS=1 uv run pytest app/tests/test_characterisation.py

Items are picked by fixed rules so the data covers the awkward cases
(no tax under 100 gp, the tax cap, missing prices) and keeps the Sets and
Magic tabs populated. Use --include to add specific item ids.
"""

from __future__ import annotations

import argparse
import json
import time
from pathlib import Path
from typing import Any

import httpx
from osrs_finance_hub.config import Settings
from osrs_finance_hub.flips import build_all_flips
from osrs_finance_hub.magic_data import BOLT_ENCHANTS, JEWELLERY_ENCHANTS, RUNE_IDS
from osrs_finance_hub.set_data import ITEM_SETS
from osrs_finance_hub.wiki_api import BASE_URL

OUT_DIR = Path(__file__).parent / "wiki"
CONTACT = "joeybricks"


def fetch_all(user_agent: str) -> dict[str, Any]:
    with httpx.Client(base_url=BASE_URL, headers={"User-Agent": user_agent}, timeout=30) as client:
        raw = {}
        for name in ("mapping", "latest", "1h", "5m"):
            response = client.get(f"/{name}")
            response.raise_for_status()
            raw[name] = response.json()
        return raw


def top(ids: list[int], key, n: int) -> list[int]:
    """The first n ids by key, highest first, ties broken by id."""
    return sorted(ids, key=lambda i: (-key(i), i))[:n]


def choose_ids(raw: dict[str, Any], now: int) -> set[int]:
    mapping_ids = sorted(item["id"] for item in raw["mapping"])
    latest = raw["latest"]["data"]
    hourly = raw["1h"]["data"]

    def price(i: int, side: str) -> int | None:
        return latest.get(str(i), {}).get(side)

    def volume(i: int) -> int:
        row = hourly.get(str(i), {})
        return (row.get("highPriceVolume") or 0) + (row.get("lowPriceVolume") or 0)

    priced = [i for i in mapping_ids if price(i, "high") and price(i, "low")]

    chosen: set[int] = set(RUNE_IDS.values())
    for enchant in (*JEWELLERY_ENCHANTS, *BOLT_ENCHANTS):
        chosen |= {enchant.input_id, enchant.output_id}

    # One whole set from each category, so the Sets tab has rows to price.
    seen_categories: set[str] = set()
    for category, (set_id, _), pieces in ITEM_SETS:
        if category not in seen_categories:
            seen_categories.add(category)
            chosen |= {set_id, *(piece_id for piece_id, _ in pieces)}

    # What the scanner would show at the top right now.
    flips = build_all_flips(raw["mapping"], latest, hourly, now=now, volume_fraction=0.10)
    chosen |= {f.id for f in flips[:30]}

    chosen |= set(top(priced, volume, 30))                                  # busiest
    chosen |= set(top(priced, lambda i: price(i, "high"), 15))              # tax cap
    chosen |= set(top([i for i in priced if price(i, "high") < 100], volume, 15))  # no tax
    one_sided = [i for i in mapping_ids if str(i) in latest and not (price(i, "high") and price(i, "low"))]
    chosen |= set(one_sided[:10])
    chosen |= set([i for i in mapping_ids if str(i) not in latest][:10])    # never traded
    chosen |= set([i for i in priced if str(i) not in hourly][:10])         # no 1h volume
    return chosen & set(mapping_ids)


def trim(raw: dict[str, Any], ids: set[int]) -> dict[str, Any]:
    keep = {str(i) for i in ids}
    out: dict[str, Any] = {"mapping": [item for item in raw["mapping"] if item["id"] in ids]}
    for name in ("latest", "1h", "5m"):
        payload = dict(raw[name])
        payload["data"] = {k: v for k, v in raw[name]["data"].items() if k in keep}
        out[name] = payload
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--include", default="", help="comma-separated item ids to keep as well")
    args = parser.parse_args()

    user_agent = Settings(contact=CONTACT).user_agent
    recorded_at = int(time.time())
    raw = fetch_all(user_agent)

    ids = choose_ids(raw, recorded_at)
    ids |= {int(i) for i in args.include.split(",") if i.strip()} & {item["id"] for item in raw["mapping"]}
    trimmed = trim(raw, ids)

    OUT_DIR.mkdir(exist_ok=True)
    for name, payload in trimmed.items():
        (OUT_DIR / f"{name}.json").write_text(json.dumps(payload, indent=1, sort_keys=True) + "\n")
    meta = {"recorded_at": recorded_at, "item_count": len(ids), "source": BASE_URL}
    (OUT_DIR / "meta.json").write_text(json.dumps(meta, indent=1) + "\n")
    print(f"kept {len(ids)} of {len(raw['mapping'])} items -> {OUT_DIR}")


if __name__ == "__main__":
    main()
