"""Trace d'un tour : ce qui s'est passé, dans l'ordre, pour pouvoir l'expliquer après coup.

Les reçus disent ce que les outils ont fait ; la consommation dit ce que le modèle a coûté. Ce
qu'ils ne disent pas, c'est pourquoi une réponse a été refusée ou corrigée. La trace garde cela,
événement par événement, dans la base (commande `atlas tour`). Elle ne contient jamais le message
de l'utilisateur ; les phrases du modèle n'y figurent que lorsqu'elles expliquent un refus (la
phrase qui a affirmé une action) ou en aperçu court de la réponse finale.
"""

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field


class EventKind(StrEnum):
    """Nature d'un événement de la trace."""

    START = "start"  # début du tour
    MODEL_CALL = "model_call"  # un appel au modèle et ce qu'il a demandé
    RECEIPT = "receipt"  # un reçu créé par un outil ou une décision humaine
    REFUSAL = "refusal"  # une réponse refusée, corrigée ou jugée non conforme
    FINAL = "final"  # l'issue du tour


class Reason(StrEnum):
    """Raison pour laquelle une réponse du modèle n'a pas été acceptée telle quelle."""

    NON_CONFORMING = "non_conforming"  # texte libre au lieu de l'outil respond
    INVALID_RECEIPT_NUMBERS = "invalid_receipt_numbers"  # numéros de reçus refusés
    UNPROVED_CLAIMS = "unproved_claims"  # affirmation détectée sans reçu qui la prouve
    INVALID_RESPOND_ARGUMENTS = "invalid_respond_arguments"  # respond sans message utilisable
    RESPOND_WITH_OTHER_TOOLS = "respond_with_other_tools"  # respond n'était pas seul
    EMPTY_RESPONSE = "empty_response"  # ni texte ni outil


class Action(StrEnum):
    """Ce que l'agent a fait de la réponse refusée."""

    RETRY = "retry"  # une correction a été renvoyée au modèle
    REMOVED = "removed"  # les numéros invalides ont été retirés, la réponse est gardée
    BLOCKED = "blocked"  # le message a été remplacé par un refus
    FALLBACK = "fallback"  # réponse gardée, contrôlée par le détecteur seul
    IGNORED = "ignored"  # l'appel a été ignoré
    FAILED = "failed"  # le tour s'est arrêté en erreur


class TraceEvent(BaseModel):
    """Un événement d'un tour ; data est un objet JSON dont le contenu dépend de kind."""

    model_config = ConfigDict(frozen=True)

    turn_id: str
    kind: EventKind
    data: dict[str, Any] = Field(default_factory=dict)
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class TraceStore(Protocol):
    """Stockage des événements de trace."""

    def add(self, event: TraceEvent) -> None:
        """Enregistre un événement."""
        ...

    def events(self, turn_id: str) -> list[TraceEvent]:
        """Retourne les événements d'un tour, dans l'ordre d'enregistrement."""
        ...


class InMemoryTraceStore:
    """Trace en mémoire, perdue à l'arrêt du programme."""

    def __init__(self) -> None:
        self._events: list[TraceEvent] = []

    def add(self, event: TraceEvent) -> None:
        """Enregistre un événement."""
        self._events.append(event)

    def events(self, turn_id: str) -> list[TraceEvent]:
        """Retourne les événements d'un tour, dans l'ordre d'enregistrement."""
        return [event for event in self._events if event.turn_id == turn_id]
