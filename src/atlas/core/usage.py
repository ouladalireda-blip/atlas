"""Journal de consommation : tokens et coût par tour, total du jour, budget quotidien."""

import logging
from collections.abc import Callable
from datetime import date
from typing import Protocol

from pydantic import BaseModel, ConfigDict

from atlas.llm.base import Usage

logger = logging.getLogger(__name__)


class UsageEntry(BaseModel):
    """Consommation d'un appel au modèle."""

    model_config = ConfigDict(frozen=True)

    day: date
    turn_id: str
    model: str
    tokens: int
    cost: float | None
    estimated: bool


class UsageStore(Protocol):
    """Stockage des appels au modèle."""

    def add(self, entry: UsageEntry) -> None:
        """Enregistre la consommation d'un appel."""
        ...

    def for_day(self, day: date) -> list[UsageEntry]:
        """Retourne les appels d'un jour."""
        ...

    def for_turn(self, turn_id: str) -> list[UsageEntry]:
        """Retourne les appels d'un tour."""
        ...

    def all(self) -> list[UsageEntry]:
        """Retourne tous les appels enregistrés."""
        ...


class InMemoryUsageStore:
    """Stockage en mémoire, perdu à l'arrêt du programme."""

    def __init__(self) -> None:
        self._entries: list[UsageEntry] = []

    def add(self, entry: UsageEntry) -> None:
        """Enregistre la consommation d'un appel."""
        self._entries.append(entry)

    def for_day(self, day: date) -> list[UsageEntry]:
        """Retourne les appels d'un jour."""
        return [entry for entry in self._entries if entry.day == day]

    def for_turn(self, turn_id: str) -> list[UsageEntry]:
        """Retourne les appels d'un tour."""
        return [entry for entry in self._entries if entry.turn_id == turn_id]

    def all(self) -> list[UsageEntry]:
        """Retourne tous les appels enregistrés."""
        return list(self._entries)


class UsageLog:
    """Journal de consommation ; le total du jour repart de zéro à minuit (heure locale).

    Si le stockage refuse une écriture, l'appel est gardé en mémoire : l'argent est dépensé,
    le budget du processus en cours doit donc en tenir compte.
    """

    def __init__(
        self,
        token_budget: int,
        cost_budget: float,
        today: Callable[[], date] = date.today,
        store: UsageStore | None = None,
    ) -> None:
        self.token_budget = token_budget
        self.cost_budget = cost_budget
        self._today = today
        self._store: UsageStore = store if store is not None else InMemoryUsageStore()
        self._unsaved: list[UsageEntry] = []

    @property
    def entries(self) -> list[UsageEntry]:
        """Tous les appels connus : ceux du stockage, puis ceux qui n'ont pu être écrits."""
        return [*self._store.all(), *self._unsaved]

    def record(self, turn_id: str, model: str, usage: Usage) -> None:
        """Ajoute la consommation d'un appel."""
        entry = UsageEntry(
            day=self._today(),
            turn_id=turn_id,
            model=model,
            tokens=usage.total_tokens,
            cost=usage.cost,
            estimated=usage.estimated,
        )
        try:
            self._store.add(entry)
        except Exception:
            logger.exception("Consommation non enregistrée pour le tour %s", turn_id)
            self._unsaved.append(entry)

    def _today_entries(self) -> list[UsageEntry]:
        today = self._today()
        return [
            *self._store.for_day(today),
            *(entry for entry in self._unsaved if entry.day == today),
        ]

    def _turn_entries(self, turn_id: str) -> list[UsageEntry]:
        return [
            *self._store.for_turn(turn_id),
            *(entry for entry in self._unsaved if entry.turn_id == turn_id),
        ]

    def tokens_today(self) -> int:
        """Tokens consommés aujourd'hui."""
        return sum(entry.tokens for entry in self._today_entries())

    def cost_today(self) -> float:
        """Coût réel connu aujourd'hui, en dollars ; les appels sans coût communiqué comptent 0."""
        return sum(entry.cost or 0.0 for entry in self._today_entries())

    def turn_tokens(self, turn_id: str) -> int:
        """Tokens consommés par un tour."""
        return sum(entry.tokens for entry in self._turn_entries(turn_id))

    def turn_cost(self, turn_id: str) -> float | None:
        """Coût d'un tour ; None si aucun appel du tour n'a communiqué de coût."""
        costs = [entry.cost for entry in self._turn_entries(turn_id) if entry.cost is not None]
        return sum(costs) if costs else None

    def exhausted(self) -> str | None:
        """Retourne la raison du refus si un budget du jour est atteint, sinon None."""
        tokens, cost = self.tokens_today(), self.cost_today()
        if tokens >= self.token_budget:
            return f"budget quotidien de tokens atteint ({tokens} sur {self.token_budget})"
        if cost >= self.cost_budget:
            return f"budget quotidien en dollars atteint (${cost:.4f} sur ${self.cost_budget:.2f})"
        return None
