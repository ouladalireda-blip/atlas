"""Contrat commun des modèles de langage : le modèle reste interchangeable."""

import json
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

# Un message suit le format OpenAI : {"role": ..., "content": ..., ...}.
type Message = dict[str, Any]


class LLMUnavailable(Exception):
    """Le modèle n'a pas pu répondre ; le message est destiné à être lu par l'utilisateur."""


class LLMConfigError(Exception):
    """La configuration du modèle est incomplète ou invalide ; refus au démarrage."""


class Usage(BaseModel):
    """Consommation d'un appel. cost est None quand le fournisseur ne la communique pas."""

    model_config = ConfigDict(frozen=True)

    prompt_tokens: int = Field(default=0, ge=0)
    completion_tokens: int = Field(default=0, ge=0)
    total_tokens: int = Field(default=0, ge=0)
    cost: float | None = Field(default=None, ge=0)
    # True si le fournisseur n'a pas communiqué de consommation et qu'elle est estimée.
    estimated: bool = False


class ToolCall(BaseModel):
    """Demande d'outil faite par le modèle.

    arguments est un dict si le JSON est lisible. Sinon c'est le texte brut, transmis tel quel
    pour que l'exécuteur produise un reçu error plutôt que d'inventer une valeur.
    """

    model_config = ConfigDict(frozen=True)

    id: str
    name: str
    arguments: Any

    def arguments_as_text(self) -> str:
        """Retourne les arguments sous forme de texte, comme l'API les attend en retour."""
        if isinstance(self.arguments, str):
            return self.arguments
        return json.dumps(self.arguments, ensure_ascii=False)


class LLMResponse(BaseModel):
    """Réponse d'un modèle : du texte, des demandes d'outils, ou les deux."""

    model_config = ConfigDict(frozen=True)

    content: str | None = None
    tool_calls: list[ToolCall] = Field(default_factory=list)
    usage: Usage = Field(default_factory=Usage)
    model: str = ""


class LLMClient(Protocol):
    """Interface commune à tous les fournisseurs."""

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        """Envoie la conversation et retourne la réponse ; lève LLMUnavailable en cas d'échec."""
        ...
