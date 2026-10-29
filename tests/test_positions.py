"""The pinned-items store."""

from __future__ import annotations

import json

import pytest

from flipfinder.positions import STORE_VERSION, Position, PositionStore, SellLot


@pytest.fixture
def store(tmp_path):
    return PositionStore(tmp_path / "nested" / "positions.json")


def test_a_fresh_store_is_empty_and_creates_no_file(tmp_path):
    path = tmp_path / "positions.json"
    store = PositionStore(path)
    assert len(store) == 0
    assert store.all() == []
    assert not path.exists(), "reading should not create the file"


def test_pinning_writes_to_disk_and_reloads(store):
    store.upsert(4151, buy_quantity=70, bought=20, buy_price=777_000)
    assert store.path.exists()

    reopened = PositionStore(store.path)
    assert len(reopened) == 1
    position = reopened.get(4151)
    assert position.buy_quantity == 70
    assert position.bought == 20
    assert position.buy_price == 777_000
    assert position.is_open_position


def test_a_pin_without_a_buy_price_is_only_a_watch(store):
    store.upsert(4151)
    position = store.get(4151)
    assert position.buy_price is None
    assert not position.is_open_position


def test_upsert_only_touches_the_fields_it_is_given(store):
    store.upsert(4151, buy_quantity=70, buy_price=777_000, note="first batch")
    store.upsert(4151, bought=35)             # just how many have arrived
    position = store.get(4151)
    assert position.bought == 35
    assert position.buy_quantity == 70, "the order size should survive"
    assert position.buy_price == 777_000, "buy price should survive"
    assert position.note == "first batch", "note should survive"


def test_clear_buy_throws_the_whole_trade_away(store):
    store.upsert(4151, buy_quantity=70, bought=70, buy_price=777_000)
    store.add_lot(4151, quantity=70, price=800_000)
    store.upsert(4151, clear_buy=True)
    position = store.get(4151)
    assert position.buy_price is None
    assert position.buy_quantity == 0
    assert position.bought == 0
    assert position.lots == [], "lots belong to the trade and go with it"
    assert not position.is_open_position


def test_removing_reports_whether_anything_was_pinned(store):
    store.upsert(4151)
    assert store.remove(4151) is True
    assert store.remove(4151) is False
    assert len(store) == 0


def test_negative_numbers_are_clamped(store):
    store.upsert(4151, buy_quantity=-5, bought=-3, buy_price=-100)
    position = store.get(4151)
    assert position.buy_quantity == 0
    assert position.bought == 0
    assert position.buy_price == 0


def test_notes_are_capped_so_the_file_cannot_balloon(store):
    store.upsert(4151, note="x" * 5_000)
    assert len(store.get(4151).note) == 500


def test_listing_is_newest_pin_first(store):
    store.upsert(1, )
    store.upsert(2, )
    store.upsert(3, )
    # pinned_at is set on creation, so the last one pinned leads.
    assert [p.item_id for p in store.all()] == [3, 2, 1]


def test_a_corrupt_file_is_reported_not_fatal(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text("{ this is not json", encoding="utf-8")
    store = PositionStore(path)
    assert len(store) == 0
    assert store.load_error is not None
    # And the store still works from here on.
    store.upsert(4151, buy_quantity=1, bought=1, buy_price=10)
    assert len(PositionStore(path)) == 1


def test_an_entry_missing_its_id_is_reported_not_fatal(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text(json.dumps({"positions": [{"quantity": 5}]}), encoding="utf-8")
    store = PositionStore(path)
    assert store.load_error is not None
    assert len(store) == 0


def test_saved_file_is_valid_json_with_a_version(store):
    store.upsert(4151, buy_quantity=70, buy_price=777_000)
    raw = json.loads(store.path.read_text("utf-8"))
    assert raw["version"] == STORE_VERSION
    assert raw["positions"][0]["item_id"] == 4151


def test_position_round_trips_through_a_dict():
    original = Position(
        item_id=4151, buy_price=777_000, buy_quantity=70, bought=30,
        reference_sell=800_000, note="hi",
        lots=[SellLot(lot_id="abc12345", quantity=30, price=801_000)],
    )
    clone = Position.from_dict(original.to_dict())
    assert clone == original


# --- sell lots ---------------------------------------------------------------

def test_lots_are_added_with_their_own_ids(store):
    store.upsert(4151, buy_price=100, buy_quantity=1_000, bought=500)
    first = store.add_lot(4151, quantity=200, price=130)
    second = store.add_lot(4151, quantity=300, price=135)
    assert first.lot_id != second.lot_id
    assert [l.quantity for l in store.get(4151).lots] == [200, 300]


def test_adding_a_lot_to_nothing_pinned_returns_none(store):
    assert store.add_lot(9999, quantity=1, price=1) is None


def test_marking_a_lot_sold_stamps_the_time(store):
    store.upsert(4151, buy_price=100)
    lot = store.add_lot(4151, quantity=200, price=130)
    assert lot.sold_at is None

    updated = store.update_lot(4151, lot.lot_id, sold=True)
    assert updated.sold is True
    assert updated.sold_at is not None

    # Marking it unsold again clears the stamp.
    reverted = store.update_lot(4151, lot.lot_id, sold=False)
    assert reverted.sold is False
    assert reverted.sold_at is None


def test_updating_a_lot_leaves_other_fields_alone(store):
    store.upsert(4151, buy_price=100)
    lot = store.add_lot(4151, quantity=200, price=130)
    store.update_lot(4151, lot.lot_id, price=140)
    refreshed = store.get(4151).lot(lot.lot_id)
    assert refreshed.price == 140
    assert refreshed.quantity == 200


def test_updating_a_missing_lot_returns_none(store):
    store.upsert(4151, buy_price=100)
    assert store.update_lot(4151, "nosuchid", sold=True) is None
    assert store.update_lot(9999, "nosuchid", sold=True) is None


def test_removing_a_lot_reports_whether_it_existed(store):
    store.upsert(4151, buy_price=100)
    lot = store.add_lot(4151, quantity=200, price=130)
    assert store.remove_lot(4151, lot.lot_id) is True
    assert store.remove_lot(4151, lot.lot_id) is False
    assert store.get(4151).lots == []


def test_lots_survive_a_reload(store):
    store.upsert(4151, buy_price=100, buy_quantity=1_000, bought=500)
    store.add_lot(4151, quantity=200, price=130)
    sold = store.add_lot(4151, quantity=300, price=135)
    store.update_lot(4151, sold.lot_id, sold=True)

    reopened = PositionStore(store.path)
    lots = reopened.get(4151).lots
    assert len(lots) == 2
    assert [l.sold for l in lots] == [False, True]
    assert lots[1].sold_at is not None


# --- upgrading an older file -------------------------------------------------

def test_a_version_one_file_is_read_and_upgraded(tmp_path):
    """The old format had one quantity and one target price.

    It could not tell an order from what had arrived, so the quantity becomes
    both, and a target price becomes a lot for the whole amount, which is what
    it meant in practice.
    """
    path = tmp_path / "positions.json"
    path.write_text(json.dumps({
        "version": 1,
        "positions": [
            {"item_id": 4151, "quantity": 70, "buy_price": 777_000,
             "target_sell": 800_000, "note": "old", "pinned_at": 1.0},
            {"item_id": 453, "quantity": 0, "buy_price": None,
             "target_sell": None, "note": "", "pinned_at": 2.0},
        ],
    }), encoding="utf-8")

    store = PositionStore(path)
    assert store.load_error is None

    traded = store.get(4151)
    assert traded.buy_quantity == 70
    assert traded.bought == 70
    assert traded.buy_price == 777_000
    assert traded.note == "old"
    assert len(traded.lots) == 1
    assert traded.lots[0].quantity == 70
    assert traded.lots[0].price == 800_000
    assert traded.lots[0].sold is False

    watch = store.get(453)
    assert watch.lots == [], "a watch with no target gains no lot"
    assert not watch.is_open_position


def test_upgrading_writes_the_new_version_back(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text(json.dumps({
        "version": 1,
        "positions": [{"item_id": 4151, "quantity": 70, "buy_price": 100}],
    }), encoding="utf-8")

    store = PositionStore(path)
    store.upsert(4151, bought=70)      # any change triggers a save
    raw = json.loads(path.read_text("utf-8"))
    assert raw["version"] == STORE_VERSION
    assert raw["positions"][0]["buy_quantity"] == 70


# --- set trades --------------------------------------------------------------

from flipfinder.positions import SetPosition  # noqa: E402


def test_a_pinned_set_trade_survives_a_reload(store):
    store.pin_set(10, "pack", buy_ids=[1, 2, 3], sell_ids=[10])
    store.update_set_buy(10, 1, buy_price=100, buy_quantity=5, bought=2,
                         reference_highs={1: 120, 2: 220, 3: 320, 10: 700})
    store.add_set_lot(10, 10, quantity=2, price=700)

    reopened = PositionStore(store.path).get_set(10)
    assert reopened.direction == "pack"
    assert [l.item_id for l in reopened.buys] == [1, 2, 3]
    leg = reopened.buy_leg(1)
    assert (leg.buy_price, leg.buy_quantity, leg.bought) == (100, 5, 2)
    assert reopened.reference_highs == {1: 120, 2: 220, 3: 320, 10: 700}
    assert reopened.sell_leg(10).lots[0].price == 700


def test_set_trades_and_item_trades_live_side_by_side(store):
    store.upsert(4151, buy_price=1, buy_quantity=1)
    store.pin_set(4151, "unpack", buy_ids=[4151], sell_ids=[1, 2])
    reopened = PositionStore(store.path)
    assert len(reopened) == 1 and len(reopened.all_sets()) == 1


def test_a_version_two_file_has_no_set_trades(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text(json.dumps({"version": 2, "positions": [{"item_id": 1}]}), "utf-8")
    store = PositionStore(path)
    assert len(store) == 1 and store.all_sets() == [] and store.load_error is None


def test_a_bad_set_trade_is_skipped_without_losing_the_rest(tmp_path):
    path = tmp_path / "positions.json"
    path.write_text(json.dumps({
        "version": 3,
        "positions": [{"item_id": 1, "buy_price": 5}],
        "set_positions": [
            {"set_id": 10, "direction": "sideways"},
            {"set_id": 20, "direction": "pack", "buys": [{"item_id": 2}], "sells": [{"item_id": 20}]},
        ],
    }), "utf-8")
    store = PositionStore(path)
    assert store.get(1).buy_price == 5
    assert [p.set_id for p in store.all_sets()] == [20]
    assert "set trade" in store.load_error


def test_the_reference_snapshot_is_only_taken_once(store):
    store.pin_set(10, "pack", buy_ids=[1], sell_ids=[10])
    store.update_set_buy(10, 1, buy_price=100, reference_highs={1: 120})
    store.update_set_buy(10, 1, buy_price=90, reference_highs={1: 999})
    assert store.get_set(10).reference_highs == {1: 120}


def test_pinning_a_set_twice_keeps_the_first(store):
    store.pin_set(10, "pack", buy_ids=[1], sell_ids=[10])
    store.update_set_buy(10, 1, bought=4)
    store.pin_set(10, "pack", buy_ids=[1], sell_ids=[10])
    assert store.get_set(10).buy_leg(1).bought == 4


def test_set_lots_can_be_sold_undone_and_removed(store):
    store.pin_set(10, "unpack", buy_ids=[10], sell_ids=[1, 2])
    lot = store.add_set_lot(10, 2, quantity=3, price=50)
    store.update_set_lot(10, 2, lot.lot_id, sold=True)
    assert store.get_set(10).sell_leg(2).lots[0].sold_at is not None
    store.update_set_lot(10, 2, lot.lot_id, sold=False)
    assert store.get_set(10).sell_leg(2).lots[0].sold_at is None
    assert store.remove_set_lot(10, 2, lot.lot_id) is True
    assert store.remove_set_lot(10, 2, lot.lot_id) is False


def test_set_changes_to_items_outside_the_trade_are_refused(store):
    store.pin_set(10, "pack", buy_ids=[1], sell_ids=[10])
    assert store.update_set_buy(10, 99, bought=1) is None
    assert store.add_set_lot(10, 1, quantity=1, price=1) is None   # 1 is bought, not sold
    assert store.update_set_buy(77, 1, bought=1) is None


def test_set_position_round_trips_through_a_dict():
    original = SetPosition(set_id=10, direction="unpack", buys=[], sells=[],
                           reference_highs={1: 5})
    assert SetPosition.from_dict(json.loads(json.dumps(original.to_dict()))) == original
