"""Stockage SQLite des reçus d'outils."""

import json
import sqlite3
from datetime import datetime
from typing import Any

from atlas.core.receipts import Receipt, ReceiptStatus


def _dump(value: Any) -> str:
    """Sérialise en JSON ; ce qui n'est pas sérialisable est conservé sous forme de texte."""
    return json.dumps(value, ensure_ascii=False, default=str)


def _to_receipt(row: sqlite3.Row) -> Receipt:
    return Receipt(
        id=row["id"],
        turn_id=row["turn_id"],
        tool=row["tool"],
        effect=row["effect"],
        args=json.loads(row["args"]),
        status=ReceiptStatus(row["status"]),
        result=json.loads(row["result"]),
        created_at=datetime.fromisoformat(row["created_at"]),
        parent_id=row["parent_id"],
    )


class SqliteReceiptStore:
    """Reçus conservés dans la base ; respecte le Protocol ReceiptStore.

    Les arguments et résultats reviennent tels que JSON les représente : un tuple devient une
    liste, une date devient son texte.
    """

    def __init__(self, connection: sqlite3.Connection) -> None:
        self._connection = connection

    def add(self, receipt: Receipt) -> Receipt:
        """Enregistre le reçu et retourne sa version dotée de l'identifiant de la base."""
        cursor = self._connection.execute(
            "INSERT INTO receipts "
            "(turn_id, tool, effect, args, status, result, created_at, parent_id) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                receipt.turn_id,
                receipt.tool,
                receipt.effect,
                _dump(receipt.args),
                receipt.status.value,
                _dump(receipt.result),
                receipt.created_at.isoformat(),
                receipt.parent_id,
            ),
        )
        if cursor.lastrowid is None:
            raise sqlite3.DatabaseError("La base n'a pas attribué d'identifiant au reçu.")
        return receipt.model_copy(update={"id": cursor.lastrowid})

    def for_turn(self, turn_id: str) -> list[Receipt]:
        """Retourne les reçus d'un tour, dans l'ordre d'enregistrement."""
        rows = self._connection.execute(
            "SELECT * FROM receipts WHERE turn_id = ? ORDER BY id", (turn_id,)
        ).fetchall()
        return [_to_receipt(row) for row in rows]

    def get(self, receipt_id: int) -> Receipt | None:
        """Retourne un reçu par identifiant, ou None."""
        row = self._connection.execute(
            "SELECT * FROM receipts WHERE id = ?", (receipt_id,)
        ).fetchone()
        return None if row is None else _to_receipt(row)
