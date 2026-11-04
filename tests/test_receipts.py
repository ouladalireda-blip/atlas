"""Tests du stockage des reçus."""

import pytest
from pydantic import ValidationError

from atlas.core.receipts import InMemoryReceiptStore, Receipt, ReceiptStatus


def make_receipt(turn_id: str) -> Receipt:
    return Receipt(turn_id=turn_id, tool="t", effect=None, args={}, status=ReceiptStatus.OK)


def test_store_assigns_incremental_ids_across_turns() -> None:
    store = InMemoryReceiptStore()

    ids = [store.add(make_receipt(turn)).id for turn in ("t1", "t2", "t1")]

    assert ids == [1, 2, 3]


def test_for_turn_returns_only_that_turn_in_order() -> None:
    store = InMemoryReceiptStore()
    for turn in ("t1", "t2", "t1"):
        store.add(make_receipt(turn))

    assert [receipt.id for receipt in store.for_turn("t1")] == [1, 3]
    assert store.for_turn("unknown") == []


def test_receipt_is_immutable_and_dated_in_utc() -> None:
    receipt = make_receipt("t1")

    assert receipt.created_at.utcoffset() is not None
    with pytest.raises(ValidationError):
        receipt.status = ReceiptStatus.ERROR  # type: ignore[misc]
