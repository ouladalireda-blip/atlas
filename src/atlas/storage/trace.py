"""Stockage SQLite de la trace des tours."""

import json
import sqlite3
from datetime import datetime

from atlas.core.trace import EventKind, TraceEvent


def _to_event(row: sqlite3.Row) -> TraceEvent:
    data = json.loads(row["data"])
    if not isinstance(data, dict):
        raise ValueError(f"Événement de trace illisible en base (n°{row['id']}).")
    return TraceEvent(
        turn_id=row["turn_id"],
        kind=EventKind(row["kind"]),
        data=data,
        created_at=datetime.fromisoformat(row["created_at"]),
    )


class SqliteTraceStore:
    """Trace conservée dans la base ; respecte le Protocol TraceStore.

    Un tour a un numéro : son rang parmi les événements `start`, dans l'ordre d'enregistrement.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, event: TraceEvent) -> None:
        """Enregistre un événement."""
        self._connection.execute(
            "INSERT INTO turn_events (turn_id, kind, data, created_at) VALUES (?, ?, ?, ?)",
            (
                event.turn_id,
                event.kind.value,
                json.dumps(event.data, ensure_ascii=False, default=str),
                event.created_at.isoformat(),
            ),
        )

    def events(self, turn_id: str) -> list[TraceEvent]:
        """Retourne les événements d'un tour, dans l'ordre d'enregistrement."""
        rows = self._connection.execute(
            "SELECT * FROM turn_events WHERE turn_id = ? ORDER BY id", (turn_id,)
        ).fetchall()
        return [_to_event(row) for row in rows]

    def count_turns(self) -> int:
        """Retourne le nombre de tours tracés."""
        row = self._connection.execute(
            "SELECT COUNT(*) FROM turn_events WHERE kind = ?", (EventKind.START.value,)
        ).fetchone()
        return int(row[0])

    def turn_id_at(self, number: int) -> str | None:
        """Retourne l'identifiant du tour n° `number` (1 = le plus ancien), ou None."""
        if number < 1:
            return None
        row = self._connection.execute(
            "SELECT turn_id FROM turn_events WHERE kind = ? ORDER BY id LIMIT 1 OFFSET ?",
            (EventKind.START.value, number - 1),
        ).fetchone()
        return None if row is None else str(row["turn_id"])

    def last_turn_id(self) -> str | None:
        """Retourne l'identifiant du dernier tour tracé, ou None."""
        row = self._connection.execute(
            "SELECT turn_id FROM turn_events WHERE kind = ? ORDER BY id DESC LIMIT 1",
            (EventKind.START.value,),
        ).fetchone()
        return None if row is None else str(row["turn_id"])

    def number_of(self, turn_id: str) -> int | None:
        """Retourne le numéro d'un tour, ou None s'il n'est pas tracé."""
        row = self._connection.execute(
            "SELECT COUNT(*) FROM turn_events WHERE kind = ? AND id <= "
            "(SELECT MIN(id) FROM turn_events WHERE kind = ? AND turn_id = ?)",
            (EventKind.START.value, EventKind.START.value, turn_id),
        ).fetchone()
        found = self._connection.execute(
            "SELECT 1 FROM turn_events WHERE kind = ? AND turn_id = ?",
            (EventKind.START.value, turn_id),
        ).fetchone()
        return int(row[0]) if found else None
