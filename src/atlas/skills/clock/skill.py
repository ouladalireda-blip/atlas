"""Compétence clock : date et heure actuelles."""

from datetime import datetime
from zoneinfo import ZoneInfo

from atlas.skills.base import Skill, tool

PARIS = ZoneInfo("Europe/Paris")


class ClockSkill(Skill):
    """Donne accès à l'horloge du système."""

    name = "clock"

    @tool()
    def now(self) -> str:
        """Retourne la date et l'heure actuelles à Paris, au format ISO 8601."""
        return datetime.now(PARIS).isoformat(timespec="seconds")
