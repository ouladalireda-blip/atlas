"""Tests des magasins SQLite : reçus et consommation, y compris après réouverture."""

import logging
import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from atlas.core.receipts import Receipt, ReceiptStatus, ReceiptStore
from atlas.core.usage import UsageLog, UsageStore
from atlas.llm.base import Usage
from atlas.storage.database import IN_MEMORY, connect
from atlas.storage.migrator import apply_migrations, core_source
from atlas.storage.receipts import SqliteReceiptStore
from atlas.storage.usage import SqliteUsageStore


def open_db(path: Path | str = IN_MEMORY) -> sqlite3.Connection:
    connection = connect(path)
    apply_migrations(connection, [core_source()])
    return connection


def receipt(**changes: Any) -> Receipt:
    base: dict[str, Any] = {
        "turn_id": "t1",
        "tool": "tasks_add_task",
        "effect": "tasks.write",
        "args": {"title": "Payer"},
        "status": ReceiptStatus.OK,
        "result": {"id": 1},
    }
    return Receipt(**{**base, **changes})


def test_sqlite_stores_satisfy_the_protocols() -> None:
    connection = open_db()
    receipts: ReceiptStore = SqliteReceiptStore(connection)
    usage: UsageStore = SqliteUsageStore(connection)

    assert receipts is not None and usage is not None


def test_database_assigns_increasing_ids() -> None:
    store = SqliteReceiptStore(open_db())

    first, second = store.add(receipt()), store.add(receipt())

    assert (first.id, second.id) == (1, 2)


def test_receipt_round_trip_keeps_every_field() -> None:
    store = SqliteReceiptStore(open_db())
    stored = store.add(receipt(parent_id=None, result=["a", {"b": None}], args={"é": "ü"}))

    (loaded,) = store.for_turn("t1")

    assert loaded == stored
    assert loaded.created_at == stored.created_at
    assert loaded.args == {"é": "ü"}
    assert store.get(stored.id) == stored
    assert store.get(999) is None


def test_for_turn_filters_and_orders() -> None:
    store = SqliteReceiptStore(open_db())
    store.add(receipt(turn_id="a", tool="x1"))
    store.add(receipt(turn_id="b", tool="y"))
    store.add(receipt(turn_id="a", tool="x2"))

    assert [r.tool for r in store.for_turn("a")] == ["x1", "x2"]
    assert store.for_turn("inconnu") == []


def test_parent_link_and_statuses_are_stored() -> None:
    store = SqliteReceiptStore(open_db())
    pending = store.add(receipt(status=ReceiptStatus.PENDING_APPROVAL, result=None))
    rejected = store.add(receipt(status=ReceiptStatus.REJECTED, parent_id=pending.id))

    loaded = store.for_turn("t1")

    assert [r.status for r in loaded] == [ReceiptStatus.PENDING_APPROVAL, ReceiptStatus.REJECTED]
    assert loaded[1].parent_id == pending.id == rejected.parent_id


def test_unreadable_arguments_and_odd_results_are_kept() -> None:
    store = SqliteReceiptStore(open_db())
    store.add(receipt(args='{"title": "oups', result=date(2026, 11, 4), status=ReceiptStatus.ERROR))

    (loaded,) = store.for_turn("t1")

    assert loaded.args == '{"title": "oups'
    assert loaded.result == "2026-11-04"  # non sérialisable en JSON : conservé en texte


def test_parent_must_exist() -> None:
    store = SqliteReceiptStore(open_db())

    with pytest.raises(sqlite3.IntegrityError):
        store.add(receipt(parent_id=42))


def test_quotes_and_sql_in_values_are_stored_verbatim() -> None:
    store = SqliteReceiptStore(open_db())
    hostile = "x'); DROP TABLE receipts; --"

    store.add(receipt(turn_id=hostile, args={"title": hostile}))

    (loaded,) = store.for_turn(hostile)
    assert loaded.args == {"title": hostile}
    assert store.for_turn("t1") == []


def test_receipts_survive_reopening_the_database(tmp_path: Path) -> None:
    path = tmp_path / "data" / "atlas.db"
    first = open_db(path)
    stored = SqliteReceiptStore(first).add(receipt())
    first.close()

    second = open_db(path)

    assert SqliteReceiptStore(second).for_turn("t1") == [stored]
    # La réouverture n'a rien rejoué.
    assert second.execute("SELECT COUNT(*) FROM schema_migrations").fetchone()[0] == 3


def test_corrupt_status_fails_loudly() -> None:
    connection = open_db()
    SqliteReceiptStore(connection).add(receipt())
    connection.execute("UPDATE receipts SET status = 'inconnu'")

    with pytest.raises(ValueError, match="inconnu"):
        SqliteReceiptStore(connection).for_turn("t1")


# --- consommation --------------------------------------------------------------------------


def test_usage_survives_reopening_and_feeds_the_daily_budget(tmp_path: Path) -> None:
    path = tmp_path / "atlas.db"
    day = date(2026, 11, 4)
    first = open_db(path)
    log = UsageLog(100, 1.0, today=lambda: day, store=SqliteUsageStore(first))
    log.record("t1", "m", Usage(total_tokens=60, cost=0.4))
    first.close()

    second = open_db(path)
    reopened = UsageLog(100, 1.0, today=lambda: day, store=SqliteUsageStore(second))

    assert reopened.tokens_today() == 60
    assert reopened.cost_today() == pytest.approx(0.4)
    assert reopened.turn_tokens("t1") == 60
    assert reopened.exhausted() is None
    reopened.record("t2", "m", Usage(total_tokens=40))
    assert "tokens" in (reopened.exhausted() or "")


def test_usage_is_kept_per_day() -> None:
    store = SqliteUsageStore(open_db())
    current = {"day": date(2026, 11, 4)}
    log = UsageLog(100, 1.0, today=lambda: current["day"], store=store)
    log.record("t1", "m", Usage(total_tokens=90, estimated=True))

    current["day"] = date(2026, 11, 5)

    assert log.tokens_today() == 0
    assert log.exhausted() is None
    assert [entry.estimated for entry in log.entries] == [True]
    assert len(store.for_day(date(2026, 11, 4))) == 1


def test_usage_cost_none_round_trip() -> None:
    log = UsageLog(100, 1.0, store=SqliteUsageStore(open_db()))
    log.record("t1", "m", Usage(total_tokens=5))

    assert log.turn_cost("t1") is None
    assert log.entries[0].cost is None


def test_a_failing_usage_store_does_not_lose_the_spending(
    caplog: pytest.LogCaptureFixture,
) -> None:
    connection = open_db()
    log = UsageLog(100, 1.0, store=SqliteUsageStore(connection))
    connection.execute("DROP TABLE usage_log")
    connection.execute("CREATE TABLE usage_log (x INTEGER)")  # schéma incompatible

    with caplog.at_level(logging.ERROR):
        log.record("t1", "m", Usage(total_tokens=70, cost=0.2))

    assert "Consommation non enregistrée" in caplog.text
    # Les lectures du stockage échouent aussi ici : on vérifie le repli en mémoire seul.
    assert [entry.tokens for entry in log._unsaved] == [70]
