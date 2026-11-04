"""Registre d'outils. Chaque exécution produit un reçu journalisé dans `actions`.

`effect` déclare ce que l'outil change dans le monde (None = lecture seule).
C'est ce champ que le vérificateur compare aux affirmations de la réponse.
"""
from __future__ import annotations

import json
import traceback
from dataclasses import dataclass, field
from typing import Any, Callable

from ..db import Store


@dataclass
class ToolContext:
    store: Store
    memory: Any
    session_id: str
    turn_id: str
    tz: str


@dataclass
class Tool:
    name: str
    description: str
    parameters: dict
    fn: Callable[..., dict]
    group: str
    effect: str | None = None

    def schema(self) -> dict:
        return {"type": "function", "function": {
            "name": self.name, "description": self.description, "parameters": self.parameters}}


@dataclass
class Registry:
    tools: dict[str, Tool] = field(default_factory=dict)

    def register(self, name: str, description: str, params: dict, group: str,
                 effect: str | None = None, required: list[str] | None = None):
        schema = {"type": "object", "properties": params, "required": required or [], "additionalProperties": False}

        def deco(fn):
            self.tools[name] = Tool(name, description, schema, fn, group, effect)
            return fn
        return deco

    def schemas(self, groups: set[str] | None = None) -> list[dict]:
        """Ne charge que les groupes utiles au tour : moins de tokens, moins de tentations."""
        return [t.schema() for t in sorted(self.tools.values(), key=lambda t: t.name)
                if groups is None or t.group in groups or t.group == "core"]

    def run(self, ctx: ToolContext, name: str, args: dict) -> dict:
        tool = self.tools.get(name)
        if tool is None:
            receipt = {"tool": name, "effect": None, "status": "error", "result": {"error": f"outil inconnu: {name}"}}
        else:
            try:
                result = tool.fn(ctx, **args)
                status = result.pop("_status", "ok") if isinstance(result, dict) else "ok"
            except TypeError as e:
                result, status = {"error": f"arguments invalides: {e}"}, "error"
            except Exception as e:  # l'erreur devient un reçu, pas un crash
                result, status = {"error": str(e), "trace": traceback.format_exc(limit=1)}, "error"
            receipt = {"tool": name, "effect": tool.effect, "status": status, "result": result}
        receipt["action_id"] = ctx.store.log_action(
            ctx.session_id, ctx.turn_id, name, receipt["effect"], args, receipt["result"], receipt["status"])
        return receipt


def receipt_for_model(receipt: dict) -> str:
    """Ce que le modèle voit après un appel : statut explicite + id de reçu + résultat."""
    return json.dumps({"receipt_id": receipt["action_id"], "status": receipt["status"], "result": receipt["result"]},
                      ensure_ascii=False, default=str)
