"""Stockage SQLite du journal de consommation."""

import sqlite3
from datetime import date

from atlas.core.usage import UsageEntry


def _to_entry(row: sqlite3.Row) -> UsageEntry:
    return UsageEntry(
        day=date.fromisoformat(row["day"]),
        turn_id=row["turn_id"],
        model=row["model"],
        tokens=row["tokens"],
        cost=row["cost"],
        estimated=bool(row["estimated"]),
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
