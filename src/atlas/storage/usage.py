"""Stockage SQLite du journal de consommation."""

import sqlite3
from datetime import date

from atlas.core.usage import TURN_OUTCOMES, TurnEntry, UsageEntry


def _to_entry(row: sqlite3.Row) -> UsageEntry:
    return UsageEntry(
        day=date.fromisoformat(row["day"]),
        turn_id=row["turn_id"],
        model=row["model"],
        tokens=row["tokens"],
        cost=row["cost"],
        estimated=bool(row["estimated"]),
    )


def _to_turn(row: sqlite3.Row) -> TurnEntry:
    if row["outcome"] not in TURN_OUTCOMES:
        raise ValueError(f"Issue de tour inconnue en base : {row['outcome']!r}")
    return TurnEntry(
        day=date.fromisoformat(row["day"]),
        turn_id=row["turn_id"],
        outcome=row["outcome"],
        verdict=row["verdict"],
    )


class SqliteUsageStore:
    """Consommation conservée dans la base ; respecte le Protocol UsageStore."""

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, entry: UsageEntry) -> None:
        """Enregistre la consommation d'un appel."""
        self._connection.execute(
            "INSERT INTO usage_log (day, turn_id, model, tokens, cost, estimated) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (
                entry.day.isoformat(),
                entry.turn_id,
                entry.model,
                entry.tokens,
                entry.cost,
                int(entry.estimated),
            ),
        )

    def for_day(self, day: date) -> list[UsageEntry]:
        """Retourne les appels d'un jour."""
        rows = self._connection.execute(
            "SELECT * FROM usage_log WHERE day = ? ORDER BY id", (day.isoformat(),)
        ).fetchall()
        return [_to_entry(row) for row in rows]

    def for_turn(self, turn_id: str) -> list[UsageEntry]:
        """Retourne les appels d'un tour."""
        rows = self._connection.execute(
            "SELECT * FROM usage_log WHERE turn_id = ? ORDER BY id", (turn_id,)
        ).fetchall()
        return [_to_entry(row) for row in rows]

    def all(self) -> list[UsageEntry]:
        """Retourne tous les appels enregistrés."""
        rows = self._connection.execute("SELECT * FROM usage_log ORDER BY id").fetchall()
        return [_to_entry(row) for row in rows]

    def add_turn(self, entry: TurnEntry) -> None:
        """Enregistre l'issue d'un tour (une seule ligne par tour)."""
        self._connection.execute(
            "INSERT INTO turn_log (turn_id, day, outcome, verdict) VALUES (?, ?, ?, ?)",
            (entry.turn_id, entry.day.isoformat(), entry.outcome, entry.verdict),
        )

    def turns(self) -> list[TurnEntry]:
        """Retourne les issues de tous les tours enregistrés, dans l'ordre."""
        rows = self._connection.execute("SELECT * FROM turn_log ORDER BY rowid").fetchall()
        return [_to_turn(row) for row in rows]
