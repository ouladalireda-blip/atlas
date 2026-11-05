"""Tests du taux de conformité : journal en mémoire et SQLite, affichage de l'encart et `stats`."""

import logging
import sqlite3
from datetime import date, timedelta
from pathlib import Path
from typing import Any

import pytest
from rich.console import Console
from typer.testing import CliRunner

from atlas.core.agent import NON_CONFORMING, TurnResult, Verdict
from atlas.core.receipts import Receipt, ReceiptStatus
from atlas.core.usage import (
    Conformity,
    InMemoryUsageStore,
    TurnEntry,
    UsageLog,
    UsageStore,
)
from atlas.interfaces import cli
from atlas.storage.database import IN_MEMORY, connect
from atlas.storage.migrator import apply_migrations, core_source
from atlas.storage.usage import SqliteUsageStore

runner = CliRunner()
TODAY = date(2026, 11, 5)


def open_db(path: Path | str = IN_MEMORY) -> sqlite3.Connection:
    connection = connect(path)
    apply_migrations(connection, [core_source()])
    return connection


def entry(turn: str, outcome: Any = "respond", day: date = TODAY) -> TurnEntry:
    return TurnEntry(day=day, turn_id=turn, outcome=outcome, verdict="verified")


# --- Conformity --------------------------------------------------------------------------------


def test_rate_is_the_share_of_answered_turns_that_ended_with_respond() -> None:
    conformity = Conformity(respond=9, free_text=1, error=5)

    assert conformity.answered == 10
    assert conformity.rate == pytest.approx(0.9)


def test_rate_is_not_measurable_without_an_answered_turn() -> None:
    assert Conformity().rate is None
    assert Conformity(error=4).rate is None


# --- journal en mémoire ------------------------------------------------------------------------


def test_usage_log_counts_turns_by_outcome_and_by_day() -> None:
    log = UsageLog(1_000, 1.0, today=lambda: TODAY)
    log.record_turn("a", "respond", "verified")
    log.record_turn("b", "free_text", "no_claims")
    log.record_turn("c", "error", "error")
    older = UsageLog(1_000, 1.0, today=lambda: TODAY - timedelta(days=1), store=log._store)
    older.record_turn("d", "respond", "verified")

    total, today = log.conformity(), log.conformity(TODAY)

    assert (total.respond, total.free_text, total.error) == (2, 1, 1)
    assert (today.respond, today.free_text, today.error) == (1, 1, 1)
    assert log.conformity(TODAY + timedelta(days=1)) == Conformity()


class FailingTurnStore(InMemoryUsageStore):
    def add_turn(self, entry: TurnEntry) -> None:
        raise sqlite3.OperationalError("disque plein")


def test_a_turn_that_cannot_be_written_is_kept_in_memory_and_logged(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log = UsageLog(1_000, 1.0, today=lambda: TODAY, store=FailingTurnStore())

    with caplog.at_level(logging.ERROR):
        log.record_turn("a", "respond", "verified")

    assert log.conformity().respond == 1
    assert "non enregistrée" in caplog.text


def test_in_memory_store_satisfies_the_protocol() -> None:
    store: UsageStore = InMemoryUsageStore()
    store.add_turn(entry("a"))

    assert [t.turn_id for t in store.turns()] == ["a"]


# --- SQLite -------------------------------------------------------------------------------------


def test_sqlite_round_trip_of_turn_outcomes() -> None:
    store = SqliteUsageStore(open_db())
    store.add_turn(entry("a", "respond"))
    store.add_turn(entry("b", "free_text"))

    assert store.turns() == [entry("a", "respond"), entry("b", "free_text")]


def test_sqlite_survives_reopening(tmp_path: Path) -> None:
    path = tmp_path / "data" / "atlas.db"
    first = open_db(path)
    SqliteUsageStore(first).add_turn(entry("a"))
    first.close()

    second = open_db(path)

    assert [t.turn_id for t in SqliteUsageStore(second).turns()] == ["a"]


def test_a_turn_is_recorded_once() -> None:
    store = SqliteUsageStore(open_db())
    store.add_turn(entry("a"))

    with pytest.raises(sqlite3.IntegrityError):
        store.add_turn(entry("a"))


def test_the_database_refuses_an_unknown_outcome() -> None:
    connection = open_db()

    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO turn_log VALUES ('a', '2026-11-05', 'inconnue', 'x')")


def test_a_corrupt_outcome_in_the_database_fails_loudly() -> None:
    connection = open_db()
    connection.execute("PRAGMA ignore_check_constraints = ON")
    connection.execute("INSERT INTO turn_log VALUES ('a', '2026-11-05', 'bizarre', 'verified')")

    with pytest.raises(ValueError, match="bizarre"):
        SqliteUsageStore(connection).turns()


def test_a_duplicate_write_through_the_log_is_kept_in_memory(
    caplog: pytest.LogCaptureFixture,
) -> None:
    log = UsageLog(1_000, 1.0, today=lambda: TODAY, store=SqliteUsageStore(open_db()))
    log.record_turn("a", "respond", "verified")

    with caplog.at_level(logging.ERROR):
        log.record_turn("a", "respond", "verified")

    assert "non enregistrée" in caplog.text
    assert log.conformity().respond == 2


# --- encart « Actions de ce tour » ---------------------------------------------------------------


def make_receipt(number: int, status: ReceiptStatus, **changes: Any) -> Receipt:
    base: dict[str, Any] = {
        "id": number,
        "turn_id": "t",
        "tool": "agenda_add",
        "effect": "calendar.write",
        "args": {"title": "Dentiste", "when": "demain"},
        "status": status,
    }
    return Receipt(**{**base, **changes})


def make_result(
    reply: str = "Réponse.",
    receipts: list[Receipt] | None = None,
    verdict: Verdict = Verdict.VERIFIED,
    **changes: Any,
) -> TurnResult:
    return TurnResult(
        turn_id="t",
        reply=reply,
        receipts=receipts or [],
        verdict=verdict,
        tokens=5,
        cost=None,
        **changes,
    )


def render(result: TurnResult) -> str:
    console = Console(force_terminal=False, width=110, record=True)
    console.print(cli.render_turn(result, UsageLog(100, 1.0)))
    return console.export_text()


def test_the_panel_lists_tool_arguments_status_and_number() -> None:
    receipts = [
        make_receipt(7, ReceiptStatus.OK),
        make_receipt(8, ReceiptStatus.ERROR, tool="agenda_boom", args={}),
    ]

    output = render(make_result(receipts=receipts))

    assert "Actions de ce tour" in output
    assert "n°7" in output and "agenda_add" in output and "ok" in output
    assert "title=Dentiste, when=demain" in output
    assert "n°8" in output and "agenda_boom" in output and "(aucun)" in output and "error" in output


def test_the_panel_comes_from_the_receipts_not_from_the_message_or_the_cited_actions() -> None:
    # Le message prétend envoyer un mail et cite un numéro inexistant : l'encart l'ignore.
    result = make_result(
        reply="J'ai envoyé le mail à Sam (reçu n°99).",
        receipts=[make_receipt(7, ReceiptStatus.OK)],
        actions=[99],
        structured=True,
    )

    output = render(result)

    panel = output[output.index("Actions de ce tour") :]
    assert "n°7" in panel and "agenda_add" in panel
    assert "n°99" not in panel and "mail" not in panel
    # Le message est séparé de l'encart, au-dessus.
    assert output.index("J'ai envoyé le mail") < output.index("Actions de ce tour")


def test_without_receipts_there_is_no_panel() -> None:
    assert "Actions de ce tour" not in render(make_result())


def test_an_approval_shows_both_receipts_and_their_link() -> None:
    receipts = [
        make_receipt(2, ReceiptStatus.PENDING_APPROVAL),
        make_receipt(3, ReceiptStatus.OK, parent_id=2),
    ]

    output = render(make_result(receipts=receipts))

    assert "pending_approval" in output
    assert "n°3 ← n°2" in output


def test_warnings_are_visible() -> None:
    result = make_result(
        warnings=[NON_CONFORMING, "Numéros d'actions retirés car invalides : n°9."]
    )

    output = render(result)

    assert "⚠ Le modèle n'a pas terminé son tour par l'outil respond" in output
    assert "⚠ Numéros d'actions retirés car invalides : n°9." in output
    assert NON_CONFORMING.startswith("Le modèle")


def test_a_blocked_message_shows_an_explicit_warning() -> None:
    output = render(make_result(verdict=Verdict.BLOCKED))

    assert "⚠ Le message affirmait une action qu'aucun reçu ne prouve" in output


def test_a_clean_turn_has_no_warning() -> None:
    assert "⚠" not in render(make_result())


def test_arguments_summary() -> None:
    assert cli.summarize_arguments({}) == "(aucun)"
    assert cli.summarize_arguments(None) == "(aucun)"
    assert cli.summarize_arguments({"a": 1, "b": "x\ny"}) == "a=1, b=x y"
    assert cli.summarize_arguments({"l": [1, 2]}) == "l=[1, 2]"
    assert cli.summarize_arguments("texte brut illisible") == "texte brut illisible"
    long = cli.summarize_arguments({"note": "x" * 200}, width=20)
    assert len(long) == 20 and long.endswith("…")


# --- atlas stats --------------------------------------------------------------------------------


def stats_env(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in list(__import__("os").environ):
        if name.startswith("ATLAS_"):
            monkeypatch.delenv(name)
    path = tmp_path / "data" / "atlas.db"
    monkeypatch.setenv("ATLAS_DATABASE_PATH", str(path))
    return path


def test_stats_without_a_database_says_so_and_creates_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = stats_env(monkeypatch, tmp_path)

    result = runner.invoke(cli.app, ["stats"])

    assert result.exit_code == 0
    assert "atlas chat" in result.output
    assert not path.exists()


def test_stats_shows_counts_and_the_rate_for_today_and_in_total(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = stats_env(monkeypatch, tmp_path)
    connection = open_db(path)
    store = SqliteUsageStore(connection)
    yesterday = date.today() - timedelta(days=1)
    for number, outcome in enumerate(["respond"] * 3 + ["free_text"]):
        store.add_turn(entry(f"today{number}", outcome, date.today()))
    for number in range(4):
        store.add_turn(entry(f"old{number}", "respond", yesterday))
    store.add_turn(entry("err", "error", date.today()))
    connection.close()

    result = runner.invoke(cli.app, ["stats"])

    assert result.exit_code == 0
    assert "Conformité de l'outil respond" in result.output
    assert "75.0% (3/4)*" in result.output
    assert "87.5% (7/8)*" in result.output
    assert "* effectif insuffisant" in result.output
    assert "Consommation du jour" in result.output


def test_stats_on_an_empty_journal_invents_no_rate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = stats_env(monkeypatch, tmp_path)
    open_db(path).close()

    result = runner.invoke(cli.app, ["stats"])

    assert result.exit_code == 0
    assert "non mesurable (0/0)" in result.output


def test_stats_migrates_a_database_created_before_the_journal(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = stats_env(monkeypatch, tmp_path)
    connect(path).close()  # base neuve, sans aucune migration

    result = runner.invoke(cli.app, ["stats"])

    assert result.exit_code == 0
    assert "non mesurable (0/0)" in result.output
    assert "turn_log" in {
        row[0] for row in open_db(path).execute("SELECT name FROM sqlite_master WHERE type='table'")
    }


def test_stats_with_a_marked_threshold_of_ten_answered_turns(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    path = stats_env(monkeypatch, tmp_path)
    connection = open_db(path)
    store = SqliteUsageStore(connection)
    for number in range(10):
        store.add_turn(entry(f"t{number}", "respond"))
    connection.close()

    result = runner.invoke(cli.app, ["stats"])

    assert "100.0% (10/10)" in result.output
    assert "100.0% (10/10)*" not in result.output


def test_stats_refuses_an_invalid_configuration(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    stats_env(monkeypatch, tmp_path)
    monkeypatch.setenv("ATLAS_CLAIM_DETECTOR", "magie")

    result = runner.invoke(cli.app, ["stats"])

    assert result.exit_code == 1
    assert "Configuration invalide" in result.output


def test_stats_reports_failed_migrations(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    from atlas.storage.migrator import MigrationError

    stats_env(monkeypatch, tmp_path)
    open_db(tmp_path / "data" / "atlas.db").close()

    def failing(*args: Any, **kwargs: Any) -> None:
        raise MigrationError("migration cassée")

    monkeypatch.setattr(cli, "apply_migrations", failing)

    result = runner.invoke(cli.app, ["stats"])

    assert result.exit_code == 1
    assert "Migrations en échec" in result.output and "migration cassée" in result.output
