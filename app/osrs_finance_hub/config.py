"""Runtime settings.

Everything tunable lives here. Values come from environment variables
(prefixed OSRS_FINANCE_HUB_) and can be overridden by CLI flags.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

from . import __version__

APP_NAME = "osrs-finance-hub"

DEFAULT_POSITIONS_FILE = Path.home() / ".osrs_finance_hub" / "positions.json"
# Where trades lived when the app was called Flip Finder. Copied across once.
LEGACY_POSITIONS_FILE = Path.home() / ".flipfinder" / "positions.json"


@dataclass
class Settings:
    # Contact string sent in the User-Agent. The wiki asks for one so they can
    # reach you if the app misbehaves. An email or Discord handle is fine.
    contact: str = ""

    # How often (seconds) to refresh each endpoint.
    latest_refresh_seconds: int = 60       # /latest
    hourly_refresh_seconds: int = 300      # /1h and /5m
    timeseries_cache_seconds: int = 60     # per item+timestep, fetched on demand

    # Fraction of the 4h traded volume we assume we can capture. You are
    # competing with other flippers, so 100% is unrealistic.
    volume_fraction: float = 0.10

    # Share of the post-tax margin to concede when suggesting offer prices,
    # split evenly between the buy and the sell. Higher fills faster and earns
    # less. Measured against profit rather than the raw spread, because on an
    # expensive item the tax is most of the spread. See flips.py.
    aggression: float = 0.25

    # A suggestion must clear at least this much profit per unit after tax,
    # otherwise the item gets no suggested prices at all.
    min_profit_per_unit: int = 1

    # Timestep used for the safety rating. 24h gives a full year of daily
    # points, which is the right window for "has this been sliding for weeks".
    trend_timestep: str = "24h"

    # How many of the top scanner rows may be rated in one go. Trend data has
    # no bulk endpoint, so this is a deliberate ceiling on per-item requests.
    trend_batch_size: int = 25

    # Where the pinned items and open positions are kept. This is your own
    # record rather than cached API data, so it survives restarts.
    positions_file: Path = DEFAULT_POSITIONS_FILE

    # Web server.
    host: str = "127.0.0.1"
    port: int = 8080
    open_browser: bool = True

    @classmethod
    def from_env(cls) -> Settings:
        """Build settings from OSRS_FINANCE_HUB_* environment variables."""
        env = os.environ
        return cls(
            contact=env.get("OSRS_FINANCE_HUB_CONTACT", ""),
            latest_refresh_seconds=int(env.get("OSRS_FINANCE_HUB_LATEST_REFRESH", 60)),
            hourly_refresh_seconds=int(env.get("OSRS_FINANCE_HUB_HOURLY_REFRESH", 300)),
            timeseries_cache_seconds=int(env.get("OSRS_FINANCE_HUB_TIMESERIES_CACHE", 60)),
            volume_fraction=float(env.get("OSRS_FINANCE_HUB_VOLUME_FRACTION", 0.10)),
            aggression=float(env.get("OSRS_FINANCE_HUB_AGGRESSION", 0.25)),
            min_profit_per_unit=int(env.get("OSRS_FINANCE_HUB_MIN_PROFIT_PER_UNIT", 1)),
            trend_timestep=env.get("OSRS_FINANCE_HUB_TREND_TIMESTEP", "24h"),
            trend_batch_size=int(env.get("OSRS_FINANCE_HUB_TREND_BATCH_SIZE", 25)),
            positions_file=Path(
                env.get("OSRS_FINANCE_HUB_POSITIONS_FILE",
                        Path.home() / ".osrs_finance_hub" / "positions.json")
            ).expanduser(),
            host=env.get("OSRS_FINANCE_HUB_HOST", "127.0.0.1"),
            port=int(env.get("OSRS_FINANCE_HUB_PORT", 8080)),
            open_browser=env.get("OSRS_FINANCE_HUB_OPEN_BROWSER", "1") not in ("0", "false", "no"),
        )

    @property
    def user_agent(self) -> str:
        contact = self.contact or "contact not configured"
        return f"{APP_NAME}/{__version__} (contact: {contact})"
