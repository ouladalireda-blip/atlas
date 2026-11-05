"""Modèle du fichier manifest.yaml d'une compétence."""

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field, StringConstraints, field_validator

from atlas.core.lexicon import VERB_CLASSES
from atlas.skills.base import EFFECT_PATTERN

type Effect = Annotated[str, StringConstraints(pattern=EFFECT_PATTERN)]
type ClaimWord = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class Claims(BaseModel):
    """Le domaine d'une compétence : ses objets et ce que chaque classe de verbe y produit.

    Le cœur connaît les verbes (write, delete, send) ; la compétence connaît ses objets (nouns) et
    dit, pour chaque classe qu'elle gère, quel effet elle produit (actions). Une liste vide est
    refusée : elle désactiverait la vérification sans le dire.
    """

    model_config = ConfigDict(extra="forbid")

    nouns: Annotated[list[ClaimWord], Field(min_length=1)]
    actions: Annotated[dict[str, Effect], Field(min_length=1)]

    @field_validator("actions")
    @classmethod
    def check_classes(cls, actions: dict[str, str]) -> dict[str, str]:
        """Refuse une classe de verbes que le cœur ne connaît pas."""
        unknown = sorted(set(actions) - VERB_CLASSES)
        if unknown:
            raise ValueError(
                f"classes de verbes inconnues : {', '.join(unknown)} "
                f"(connues : {', '.join(sorted(VERB_CLASSES))})"
            )
        return actions


class SkillManifest(BaseModel):
    """Métadonnées d'une compétence ; une clé inconnue est refusée pour détecter les fautes."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str
    description: str
    examples: list[str]
    requires_approval: list[Effect]
    # Absent : les affirmations de cette compétence ne sont pas vérifiées.
    claims: Claims | None = None
