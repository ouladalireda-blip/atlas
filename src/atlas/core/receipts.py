"""Reçus d'outils : la trace de ce qui a réellement été tenté et de son issue."""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class ReceiptStatus(StrEnum):
    """Issue d'une demande d'exécution d'outil."""

    OK = "ok"
    ERROR = "error"
    PENDING_APPROVAL = "pending_approval"
    REJECTED = "rejected"


class Receipt(BaseModel):
    """Reçu immuable ; id vaut 0 tant que le store ne l'a pas enregistré.

    parent_id relie le reçu d'une décision humaine (ok, error ou rejected) au reçu
    pending_approval qui l'a provoquée.
    """

    model_config = ConfigDict(frozen=True)

    id: int = 0
    turn_id: str
    tool: str
    effect: str | None
    # args reste libre : un reçu doit pouvoir consigner des arguments invalides.
    args: Any
    status: ReceiptStatus
    result: Any = None
    parent_id: int | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class ReceiptStore(Protocol):
    """Stockage des reçus ; c'est lui qui attribue les identifiants."""

    def add(self, receipt: Receipt) -> Receipt:
        """Enregistre le reçu et retourne sa version dotée d'un identifiant."""
        ...

    def for_turn(self, turn_id: str) -> list[Receipt]:
        """Retourne les reçus d'un tour, dans l'ordre d'enregistrement."""
        ...


class InMemoryReceiptStore:
    """Stockage en mémoire, perdu à l'arrêt du programme."""

    def __init__(self) -> None:
        self._receipts: list[Receipt] = []

    def add(self, receipt: Receipt) -> Receipt:
        """Enregistre le reçu avec l'identifiant suivant."""
        stored = receipt.model_copy(update={"id": len(self._receipts) + 1})
        self._receipts.append(stored)
        return stored

    def for_turn(self, turn_id: str) -> list[Receipt]:
        """Retourne les reçus d'un tour, dans l'ordre d'enregistrement."""
        return [receipt for receipt in self._receipts if receipt.turn_id == turn_id]
