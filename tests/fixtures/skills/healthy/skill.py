"""Compétence de test valide."""

from atlas.skills.base import Skill, tool


class HealthySkill(Skill):
    name = "healthy"

    @tool()
    def hello(self) -> str:
        """Dit bonjour."""
        return "bonjour"
