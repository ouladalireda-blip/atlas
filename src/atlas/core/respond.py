"""L'outil « respond » : la façon dont le modèle termine son tour.

Ce n'est pas une compétence : le cœur le fournit et l'agent l'intercepte, il n'exécute rien et ne
produit aucun reçu. Le modèle y met son message et les numéros des reçus des actions que ce
message rapporte ; l'agent vérifie ces numéros contre le journal des reçus, qui reste la source
de vérité (voir l'ADR 0007).

Le schéma est une constante : il ne dépend ni des compétences chargées ni du tour, pour que la
partie fixe de la requête (prompt système et outils) reste identique d'un appel à l'autre et
profite du cache de prompt du fournisseur.
"""

import copy
from typing import Any

from pydantic import BaseModel, ConfigDict, StrictInt, ValidationError, field_validator

RESPOND_TOOL_NAME = "respond"

_RESPOND_TOOL: dict[str, Any] = {
    "type": "function",
    "function": {
        "name": RESPOND_TOOL_NAME,
        "description": (
            "Termine ton tour : envoie ta réponse à l'utilisateur. À appeler seul, une fois "
            "tous les autres outils terminés. Mets dans actions les numéros (receipt_id) des "
            "reçus de CE tour dont ton message rend compte, et [] si tu n'as rien exécuté."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "message": {
                    "type": "string",
                    "minLength": 1,
                    "description": "Ta réponse à l'utilisateur, non vide.",
                },
                "actions": {
                    "type": "array",
                    "items": {"type": "integer"},
                    "description": (
                        "Numéros des reçus de ce tour (receipt_id) que le message rapporte."
                    ),
                },
            },
            "required": ["message", "actions"],
            "additionalProperties": False,
        },
    },
}


def respond_tool_schema() -> dict[str, Any]:
    """Retourne une copie du schéma de l'outil, identique à chaque appel."""
    return copy.deepcopy(_RESPOND_TOOL)


class RespondArgs(BaseModel):
    """Arguments valides de respond : un message non vide et des numéros de reçus entiers.

    Une clé `actions` absente vaut « aucune action » : c'est le choix le plus sûr, puisque le
    message devra alors n'affirmer aucune action.
    """

    model_config = ConfigDict(frozen=True, extra="ignore")

    message: str
    actions: list[StrictInt] = []

    @field_validator("message")
    @classmethod
    def check_message(cls, value: str) -> str:
        """Refuse un message vide ou fait d'espaces."""
        stripped = value.strip()
        if not stripped:
            raise ValueError("le message est vide")
        return stripped


def parse_respond(arguments: Any) -> RespondArgs | str:
    """Lit les arguments de respond ; retourne le texte de l'erreur s'ils sont inutilisables."""
    if not isinstance(arguments, dict):
        return (
            f"arguments illisibles (un objet JSON {{message, actions}} est attendu) : {arguments!r}"
        )
    try:
        return RespondArgs.model_validate(arguments)
    except ValidationError as error:
        problems = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'arguments'} : {item['msg']}"
            for item in error.errors()
        )
        return problems


def salvage_message(arguments: Any) -> str | None:
    """Retourne le message d'un appel respond dont les actions sont inutilisables, sinon None."""
    if isinstance(arguments, dict):
        message = arguments.get("message")
        if isinstance(message, str) and message.strip():
            return message.strip()
    return None
