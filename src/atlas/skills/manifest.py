"""Modèle du fichier manifest.yaml d'une compétence."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from atlas.skills.base import EFFECT_PATTERN

type Effect = Annotated[str, StringConstraints(pattern=EFFECT_PATTERN)]
type ClaimWord = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class SkillManifest(BaseModel):
    """Métadonnées d'une compétence ; une clé inconnue est refusée pour détecter les fautes."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str
    description: str
    examples: list[str]
    requires_approval: list[Effect]
    # Par effet, les mots qui signalent qu'une réponse affirme cette action. Une liste vide est
    # refusée : elle désactiverait la vérification de cet effet sans le dire.
    claims: dict[Effect, Annotated[list[ClaimWord], Field(min_length=1)]] = Field(
        default_factory=dict
    )
