"""Command line entry point: `osrs_finance_hub serve`."""

from __future__ import annotations

import argparse
import logging
import sys
import threading
import webbrowser
from pathlib import Path

import uvicorn

from . import __version__
from .config import Settings
from .server import create_app


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="osrs_finance_hub",
        description="Local OSRS Grand Exchange money-making hub.",
    )
    parser.add_argument("--version", action="version", version=f"osrs_finance_hub {__version__}")
    sub = parser.add_subparsers(dest="command", required=True)

    serve = sub.add_parser("serve", help="run the web app and open it in your browser")
    serve.add_argument("--host", help="bind address (default 127.0.0.1)")
    serve.add_argument("--port", type=int, help="port (default 8080)")
    serve.add_argument(
        "--contact",
        help="contact string for the User-Agent, e.g. an email or Discord handle",
    )
    serve.add_argument(
        "--volume-fraction",
        type=float,
        help="share of 4h volume you expect to capture (default 0.10)",
    )
    serve.add_argument(
        "--aggression",
        type=float,
        help="share of the post-tax margin to concede for a faster fill (default 0.25)",
    )
    serve.add_argument(
        "--min-profit-per-unit",
        type=int,
        help="minimum profit per unit after tax for a suggestion (default 1)",
    )
    serve.add_argument(
        "--latest-refresh", type=int, help="seconds between /latest refreshes (default 60)"
    )
    serve.add_argument(
        "--hourly-refresh", type=int, help="seconds between /1h and /5m refreshes (default 300)"
    )
    serve.add_argument(
        "--positions-file",
        help="where to keep pinned items (default ~/.osrs_finance_hub/positions.json)",
    )
    serve.add_argument("--no-browser", action="store_true", help="do not open a browser")
    serve.add_argument("--verbose", action="store_true", help="debug logging")
    return parser


def settings_from_args(args: argparse.Namespace) -> Settings:
    """Environment first, then CLI flags override whatever they set."""
    settings = Settings.from_env()
    if args.host:
        settings.host = args.host
    if args.port:
        settings.port = args.port
    if args.contact:
        settings.contact = args.contact
    if args.volume_fraction is not None:
        settings.volume_fraction = args.volume_fraction
    if args.aggression is not None:
        settings.aggression = args.aggression
    if args.min_profit_per_unit is not None:
        settings.min_profit_per_unit = args.min_profit_per_unit
    if args.latest_refresh is not None:
        settings.latest_refresh_seconds = args.latest_refresh
    if args.hourly_refresh is not None:
        settings.hourly_refresh_seconds = args.hourly_refresh
    if args.positions_file:
        settings.positions_file = Path(args.positions_file).expanduser()
    if args.no_browser:
        settings.open_browser = False
    return settings


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    settings = settings_from_args(args)

    logging.basicConfig(
        level=logging.DEBUG if getattr(args, "verbose", False) else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )

    if not settings.contact:
        print(
            "WARNING: no contact configured. The OSRS Wiki asks every app to\n"
            "identify itself with a way to get in touch. Set OSRS_FINANCE_HUB_CONTACT\n"
            "or pass --contact 'you@example.com' before using this for real.\n",
            file=sys.stderr,
        )

    url = f"http://{settings.host}:{settings.port}/"
    print(f"osrs_finance_hub serving at {url}")
    print(f"User-Agent: {settings.user_agent}")

    if settings.open_browser:
        # Fire slightly late so the server is listening when the tab opens.
        threading.Timer(1.5, lambda: webbrowser.open(url)).start()

    uvicorn.run(
        create_app(settings),
        host=settings.host,
        port=settings.port,
        log_level="debug" if getattr(args, "verbose", False) else "warning",
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
