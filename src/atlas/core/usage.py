"""Journal de consommation : tokens et coût par tour, total du jour, budget quotidien.

Il garde aussi, par tour, la façon dont il s'est terminé (outil respond ou texte libre) : le
taux de conformité du modèle se lit à côté de ce que les tours ont coûté.
"""

import logging
from collections.abc import Callable
from datetime import date
from typing import Literal, Protocol

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


# Comment un tour s'est terminé : par l'outil respond (conforme), par du texte libre (non conforme)
# ou sans réponse du modèle (budget, indisponibilité, limite d'outils : hors du calcul du taux).
type TurnOutcome = Literal["respond", "free_text", "error"]
TURN_OUTCOMES: tuple[TurnOutcome, ...] = ("respond", "free_text", "error")


class TurnEntry(BaseModel):
    """Issue d'un tour : conforme (respond), texte libre ou erreur, et son verdict."""

    model_config = ConfigDict(frozen=True)

    day: date
    turn_id: str
    outcome: TurnOutcome
    verdict: str


class Conformity(BaseModel):
    """Décompte des tours par issue ; le taux porte sur les tours où le modèle a répondu."""

    model_config = ConfigDict(frozen=True)

    respond: int = 0
    free_text: int = 0
    error: int = 0

    @property
    def answered(self) -> int:
        """Tours où le modèle a répondu : ceux qui sont conformes ou non."""
        return self.respond + self.free_text

    @property
    def rate(self) -> float | None:
        """Part des tours terminés par respond ; None sans tour mesurable."""
        return None if self.answered == 0 else self.respond / self.answered


class UsageStore(Protocol):
    """Stockage des appels au modèle et des issues de tours."""

    def add_turn(self, entry: TurnEntry) -> None:
        """Enregistre l'issue d'un tour."""
        ...

    def turns(self) -> list[TurnEntry]:
        """Retourne les issues de tous les tours enregistrés."""
        ...

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
        self._turns: list[TurnEntry] = []

    def add_turn(self, entry: TurnEntry) -> None:
        """Enregistre l'issue d'un tour."""
        self._turns.append(entry)

    def turns(self) -> list[TurnEntry]:
        """Retourne les issues de tous les tours enregistrés."""
        return list(self._turns)

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
        self._unsaved_turns: list[TurnEntry] = []

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

    def record_turn(self, turn_id: str, outcome: TurnOutcome, verdict: str) -> None:
        """Ajoute l'issue d'un tour ; en cas d'échec d'écriture, elle est gardée en mémoire."""
        entry = TurnEntry(day=self._today(), turn_id=turn_id, outcome=outcome, verdict=verdict)
        try:
            self._store.add_turn(entry)
        except Exception:
            logger.exception("Issue du tour %s non enregistrée", turn_id)
            self._unsaved_turns.append(entry)

    def conformity(self, day: date | None = None) -> Conformity:
        """Compte les tours par issue, pour un jour ou pour tous."""
        entries = [*self._store.turns(), *self._unsaved_turns]
        counts = dict.fromkeys(TURN_OUTCOMES, 0)
        for entry in entries:
            if day is None or entry.day == day:
                counts[entry.outcome] += 1
        return Conformity(**counts)

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
