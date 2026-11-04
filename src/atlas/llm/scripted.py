"""Modèle simulé pour les tests : réponses prévues à l'avance, aucun réseau."""

import copy
from collections.abc import Sequence
from typing import Any

from atlas.llm.base import LLMResponse, Message, ToolCall, Usage


class ScriptExhausted(RuntimeError):
    """Le test a demandé plus d'appels que de réponses scriptées."""


def text(content: str | None, *, tokens: int = 10, cost: float | None = None) -> LLMResponse:
    """Réponse texte scriptée."""
    return LLMResponse(
        content=content,
        usage=Usage(total_tokens=tokens, completion_tokens=tokens, cost=cost),
        model="scripted",
    )


def tool_calls(*calls: tuple[str, Any], tokens: int = 10, cost: float | None = None) -> LLMResponse:
    """Réponse scriptée qui demande des outils : chaque appel est (nom, arguments)."""
    return LLMResponse(
        tool_calls=[
            ToolCall(id=f"call_{position}", name=name, arguments=arguments)
            for position, (name, arguments) in enumerate(calls, start=1)
        ],
        usage=Usage(total_tokens=tokens, completion_tokens=tokens, cost=cost),
        model="scripted",
    )


class ScriptedLLM:
    """Rejoue les réponses dans l'ordre ; une exception de la liste est levée à son tour."""

    def __init__(self, script: Sequence[LLMResponse | Exception]) -> None:
        self._script = list(script)
        self.calls: list[list[Message]] = []
        self.tools_seen: list[list[dict[str, Any]]] = []

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        """Retourne la réponse suivante ; les messages reçus sont conservés pour les tests."""
        self.calls.append(copy.deepcopy(messages))
        self.tools_seen.append(tools)
        if len(self.calls) > len(self._script):
            raise ScriptExhausted(f"Appel {len(self.calls)} sans réponse scriptée.")
        item = self._script[len(self.calls) - 1]
        if isinstance(item, Exception):
            raise item
        return item
