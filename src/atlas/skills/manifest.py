"""Modèle du fichier manifest.yaml d'une compétence."""

from pydantic import BaseModel, ConfigDict, Field


class SkillManifest(BaseModel):
    """Métadonnées d'une compétence ; une clé inconnue est refusée pour détecter les fautes."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(pattern=r"^[a-z][a-z0-9_]*$")
    version: str
    description: str
    examples: list[str]
    requires_approval: list[str]
