"""Every price source the app or tests use must fit the PriceSource contract.

isinstance on a runtime_checkable Protocol only checks the method names
exist, not their signatures, but it catches a source that drifts out of shape.
"""

from __future__ import annotations

import pytest
from osrs_finance_hub.price_source import PriceSource
from osrs_finance_hub.wiki_api import WikiPricesClient
from test_cache import FlakyClient
from wiki_fixtures import FixtureSource


@pytest.mark.asyncio
async def test_wiki_client_is_a_price_source():
    client = WikiPricesClient(user_agent="test")  # no request is made
    try:
        assert isinstance(client, PriceSource)
    finally:
        await client.aclose()


@pytest.mark.parametrize("source", [FixtureSource, FlakyClient])
def test_test_doubles_are_price_sources(source):
    assert isinstance(source(), PriceSource)


def test_something_missing_a_method_is_not():
    class NoTimeseries:
        async def fetch_mapping(self): ...
        async def fetch_latest(self): ...
        async def fetch_average(self, window): ...

    assert not isinstance(NoTimeseries(), PriceSource)
