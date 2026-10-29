"""The set trade routes, against a stand-in cache so no request leaves the machine."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from flipfinder.config import Settings
from flipfinder.flips import build_flip
from flipfinder.positions import PositionStore
from flipfinder.server import create_app

# Bronze set (lg) and its pieces, as listed in set_data.
SET_ID = 12960
PIECES = [1155, 1117, 1075, 1189]


class StubCache:
    def __init__(self) -> None:
        prices = {SET_ID: (1000, 1100), 1155: (100, 120), 1117: (300, 330),
                  1075: (200, 220), 1189: (250, 270)}
        self.flips = [
            build_flip({"id": i, "name": f"Item {i}", "limit": 100},
                       {"low": lo, "high": hi, "lowTime": 0, "highTime": 0},
                       {}, now=0, volume_fraction=0.1)
            for i, (lo, hi) in prices.items()
        ]

    def build_flips(self):
        return self.flips


@pytest.fixture
def client(tmp_path):
    settings = Settings(positions_file=tmp_path / "positions.json")
    app = create_app(settings)
    # Not entered as a context manager, so the lifespan (and the real API
    # client it would start) never runs. We fill in its state by hand.
    app.state.cache = StubCache()
    app.state.settings = settings
    app.state.positions = PositionStore(settings.positions_file)
    return TestClient(app)


def test_pack_trade_round_trip(client):
    r = client.put(f"/api/set-positions/{SET_ID}", json={"direction": "pack"})
    assert r.status_code == 200
    assert [b["item_id"] for b in r.json()["trade"]["buys"]] == PIECES

    for piece in PIECES:
        r = client.put(f"/api/set-positions/{SET_ID}/buys/{piece}",
                       json={"buy_price": 100, "buy_quantity": 3, "bought": 3})
        assert r.status_code == 200
    # The first buy price snapshotted every item's high, set included.
    assert r.json()["position"]["reference_highs"][str(SET_ID)] == 1100

    r = client.post(f"/api/set-positions/{SET_ID}/sells/{SET_ID}/lots",
                    json={"quantity": 2, "price": 1100})
    lot_id = r.json()["position"]["sells"][0]["lots"][0]["lot_id"]
    r = client.put(f"/api/set-positions/{SET_ID}/sells/{SET_ID}/lots/{lot_id}",
                   json={"sold": True})
    trade = r.json()["trade"]
    assert trade["complete_sets"] == 3
    assert trade["realised_net"] == 2 * (1100 - 22 - 400)

    listing = client.get("/api/positions").json()
    assert [s["position"]["set_id"] for s in listing["set_positions"]] == [SET_ID]
    assert listing["set_positions"][0]["items"][str(SET_ID)]["high"] == 1100

    assert client.delete(f"/api/set-positions/{SET_ID}").json()["removed"] is True
    assert client.get("/api/positions").json()["set_positions"] == []


def test_bad_set_requests_are_refused(client):
    assert client.put("/api/set-positions/1", json={"direction": "pack"}).status_code == 404
    assert client.put(f"/api/set-positions/{SET_ID}", json={}).status_code == 422
    client.put(f"/api/set-positions/{SET_ID}", json={"direction": "unpack"})
    # Already pinned the other way round.
    assert client.put(f"/api/set-positions/{SET_ID}",
                      json={"direction": "pack"}).status_code == 409
    # Unpacking sells the pieces, so the set has no lots and a piece has no buy.
    assert client.post(f"/api/set-positions/{SET_ID}/sells/{SET_ID}/lots",
                       json={"quantity": 1, "price": 1}).status_code == 404
    assert client.put(f"/api/set-positions/{SET_ID}/buys/1155",
                      json={"bought": 1}).status_code == 404
    assert client.post(f"/api/set-positions/{SET_ID}/sells/1155/lots",
                       json={"quantity": 0, "price": 1}).status_code == 422
    assert client.put("/api/set-positions/999/buys/1", json={}).status_code == 404
