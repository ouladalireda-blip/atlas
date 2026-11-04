"""Journal de consommation : tokens et coût par tour, total du jour, budget quotidien."""

from collections.abc import Callable
from datetime import date

from pydantic import BaseModel, ConfigDict

from atlas.llm.base import Usage


class UsageEntry(BaseModel):
    """Consommation d'un appel au modèle."""

    model_config = ConfigDict(frozen=True)

    day: date
    turn_id: str
    model: str
    tokens: int
    cost: float | None
    estimated: bool


class UsageLog:
    """Journal en mémoire ; le total du jour repart de zéro à minuit (heure locale)."""

    def __init__(
        self,
        token_budget: int,
        cost_budget: float,
        today: Callable[[], date] = date.today,
    ) -> None:
        self.token_budget = token_budget
        self.cost_budget = cost_budget
        self._today = today
        self.entries: list[UsageEntry] = []

    def record(self, turn_id: str, model: str, usage: Usage) -> None:
        """Ajoute la consommation d'un appel."""
        self.entries.append(
            UsageEntry(
                day=self._today(),
                turn_id=turn_id,
                model=model,
                tokens=usage.total_tokens,
                cost=usage.cost,
                estimated=usage.estimated,
            )
        )

    def _today_entries(self) -> list[UsageEntry]:
        today = self._today()
        return [entry for entry in self.entries if entry.day == today]

    def tokens_today(self) -> int:
        """Tokens consommés aujourd'hui."""
        return sum(entry.tokens for entry in self._today_entries())

    def cost_today(self) -> float:
        """Coût réel connu aujourd'hui, en dollars ; les appels sans coût communiqué comptent 0."""
        return sum(entry.cost or 0.0 for entry in self._today_entries())

    def turn_tokens(self, turn_id: str) -> int:
        """Tokens consommés par un tour."""
        return sum(entry.tokens for entry in self.entries if entry.turn_id == turn_id)

    def turn_cost(self, turn_id: str) -> float | None:
        """Coût d'un tour ; None si aucun appel du tour n'a communiqué de coût."""
        costs = [
            entry.cost
            for entry in self.entries
            if entry.turn_id == turn_id and entry.cost is not None
        ]
        return sum(costs) if costs else None

    def exhausted(self) -> str | None:
        """Retourne la raison du refus si un budget du jour est atteint, sinon None."""
        if self.tokens_today() >= self.token_budget:
            return (
                f"budget quotidien de tokens atteint ({self.tokens_today()} sur "
                f"{self.token_budget})"
            )
        if self.cost_today() >= self.cost_budget:
            return (
                f"budget quotidien en dollars atteint (${self.cost_today():.4f} sur "
                f"${self.cost_budget:.2f})"
            )
        return None
