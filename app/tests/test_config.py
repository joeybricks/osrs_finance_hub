"""Settings come from defaults, then environment variables, then CLI flags."""

from __future__ import annotations

import pytest
from osrs_finance_hub.cli import build_parser, settings_from_args
from osrs_finance_hub.config import Settings


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
