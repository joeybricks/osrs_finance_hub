"""Settings come from defaults, then environment variables, then CLI flags."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from osrs_finance_hub import __version__, server
from osrs_finance_hub.cli import build_parser, settings_from_args
from osrs_finance_hub.config import Settings
from osrs_finance_hub.wiki_api import WikiPricesClient
from wiki_fixtures import FixtureSource


@pytest.fixture(autouse=True)
def clean_port_env(monkeypatch):
    monkeypatch.delenv("PORT", raising=False)
    monkeypatch.delenv("OSRS_FINANCE_HUB_PORT", raising=False)


def test_port_defaults_to_8080():
    assert Settings.from_env().port == 8080


def test_port_falls_back_to_port_env(monkeypatch):
    # Cloud Run tells the container which port to listen on through PORT.
    monkeypatch.setenv("PORT", "9000")
    assert Settings.from_env().port == 9000


def test_own_port_variable_beats_port(monkeypatch):
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("OSRS_FINANCE_HUB_PORT", "8181")
    assert Settings.from_env().port == 8181


def test_port_flag_beats_both(monkeypatch):
    monkeypatch.setenv("PORT", "9000")
    monkeypatch.setenv("OSRS_FINANCE_HUB_PORT", "8181")
    args = build_parser().parse_args(["serve", "--port", "7070"])
    assert settings_from_args(args).port == 7070


# --- User-Agent ---------------------------------------------------------------
# The wiki asks every client to identify itself with a way to get in touch.


def test_user_agent_names_app_version_and_contact():
    assert Settings(contact="joeybricks").user_agent == f"osrs-finance-hub/{__version__} (contact: joeybricks)"


def test_user_agent_without_contact_says_so():
    assert Settings(contact="").user_agent.endswith("(contact: contact not configured)")


def test_server_sends_the_configured_user_agent(monkeypatch, tmp_path):
    """The client the app builds at startup gets the User-Agent from settings."""
    seen: list[str] = []

    class RecordingClient(FixtureSource):
        def __init__(self, user_agent: str) -> None:
            seen.append(user_agent)

        async def aclose(self) -> None:
            pass

    monkeypatch.setattr(server, "WikiPricesClient", RecordingClient)
    settings = Settings(contact="joeybricks", positions_file=tmp_path / "positions.json",
                        latest_refresh_seconds=3600, hourly_refresh_seconds=3600)
    with TestClient(server.create_app(settings)):  # runs startup and shutdown
        pass
    assert seen == [f"osrs-finance-hub/{__version__} (contact: joeybricks)"]


async def test_wiki_client_puts_it_on_every_request():
    # A default header on the httpx client, so every request carries it.
    client = WikiPricesClient(user_agent="osrs-finance-hub/test (contact: joeybricks)")
    try:
        assert client._client.headers["User-Agent"] == "osrs-finance-hub/test (contact: joeybricks)"
    finally:
        await client.aclose()
