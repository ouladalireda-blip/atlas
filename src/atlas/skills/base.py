"""Contrat des compétences : déclaration des outils, schéma JSON et appel validé."""

import inspect
import re
import sqlite3
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, ClassVar, get_type_hints

from pydantic import BaseModel, ConfigDict, ValidationError, create_model

from atlas.config import Settings

# Seule définition du format d'un effet, « domaine.action » : calendar.write par exemple.
EFFECT_PATTERN = r"^[a-z][a-z0-9_]*\.[a-z][a-z0-9_]*$"

_EFFECT_ATTRIBUTE = "__atlas_tool_effect__"
_UNSUPPORTED_PARAMETER_KINDS = (
    inspect.Parameter.POSITIONAL_ONLY,
    inspect.Parameter.VAR_POSITIONAL,
    inspect.Parameter.VAR_KEYWORD,
)


class ToolDefinitionError(TypeError):
    """Un outil est mal déclaré dans le code de sa compétence."""


class ToolArgumentError(ValueError):
    """Les arguments fournis à un outil ne respectent pas son schéma."""

    def __init__(self, tool_name: str, error: ValidationError) -> None:
        details = "; ".join(
            f"{'.'.join(str(part) for part in item['loc']) or 'arguments'} : {item['msg']}"
            for item in error.errors()
        )
        super().__init__(f"Arguments invalides pour l'outil {tool_name} : {details}")
        self.tool_name = tool_name


def tool[F: Callable[..., Any]](effect: str | None = None) -> Callable[[F], F]:
    """Marque une méthode comme outil et stocke son effet, sans rien exécuter."""
    if callable(effect):
        raise ToolDefinitionError("Le décorateur s'écrit avec des parenthèses : @tool().")
    if effect is not None and not re.fullmatch(EFFECT_PATTERN, effect):
        raise ToolDefinitionError(
            f"Effet invalide : {effect!r}. Format attendu : domaine.action (calendar.write)."
        )

    def mark(method: F) -> F:
        setattr(method, _EFFECT_ATTRIBUTE, effect)
        return method

    return mark


@dataclass(frozen=True)
class ToolSpec:
    """Description complète d'un outil, prête pour un modèle et pour l'exécution."""

    name: str
    description: str
    effect: str | None
    parameters: dict[str, Any]
    method: Callable[..., Any]
    args_model: type[BaseModel]

    def to_openai(self) -> dict[str, Any]:
        """Retourne la définition de l'outil au format OpenAI."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


def _build_args_model(tool_name: str, method: Callable[..., Any]) -> type[BaseModel]:
    """Construit le modèle pydantic des arguments ; self est absent d'une méthode liée."""
    hints = get_type_hints(method, include_extras=True)
    fields: dict[str, Any] = {}
    for parameter in inspect.signature(method).parameters.values():
        if parameter.kind in _UNSUPPORTED_PARAMETER_KINDS:
            raise ToolDefinitionError(
                f"{tool_name} : le paramètre {parameter.name} doit pouvoir être passé par nom."
            )
        if parameter.name not in hints:
            raise ToolDefinitionError(
                f"{tool_name} : le paramètre {parameter.name} n'a pas de type."
            )
        required = parameter.default is inspect.Parameter.empty
        fields[parameter.name] = (hints[parameter.name], ... if required else parameter.default)
    config = ConfigDict(extra="forbid", protected_namespaces=())
    return create_model(f"{tool_name}_args", __config__=config, **fields)


def _build_spec(skill_name: str, method_name: str, method: Callable[..., Any]) -> ToolSpec:
    """Assemble le ToolSpec d'une méthode liée marquée par @tool."""
    name = f"{skill_name}_{method_name}"
    description = inspect.getdoc(method)
    if not description:
        raise ToolDefinitionError(f"{name} : la docstring est obligatoire.")
    args_model = _build_args_model(name, method)
    parameters = args_model.model_json_schema()
    parameters.pop("title", None)
    return ToolSpec(
        name=name,
        description=description,
        effect=getattr(method, _EFFECT_ATTRIBUTE),
        parameters=parameters,
        method=method,
        args_model=args_model,
    )


class SkillContextError(RuntimeError):
    """Une compétence a besoin de son contexte (base, réglages) et ne l'a pas reçu."""


@dataclass(frozen=True)
class SkillContext:
    """Ce que le cœur donne à une compétence : la connexion à la base et les réglages.

    Une compétence n'ouvre jamais la base elle-même : le cœur a déjà appliqué ses migrations.
    """

    connection: sqlite3.Connection
    settings: Settings


class Skill:
    """Classe de base d'une compétence ; les sous-classes définissent name."""

    name: ClassVar[str]

    def __init__(self, context: SkillContext | None = None) -> None:
        self.context = context

    def require_context(self) -> SkillContext:
        """Retourne le contexte, ou échoue clairement si la compétence a été créée sans lui."""
        if self.context is None:
            raise SkillContextError(
                f"La compétence {self.name} a besoin d'un contexte (base de données, réglages)."
            )
        return self.context

    def tools(self) -> list[ToolSpec]:
        """Retourne les outils marqués, sans évaluer les propriétés de l'instance."""
        return [
            _build_spec(self.name, method_name, getattr(self, method_name))
            for method_name, function in inspect.getmembers_static(type(self), inspect.isfunction)
            if hasattr(function, _EFFECT_ATTRIBUTE)
        ]


def call(spec: ToolSpec, args: dict[str, Any]) -> Any:
    """Valide et convertit les arguments, puis appelle l'outil.

    Cette fonction ne contrôle pas l'effet de l'outil : la validation humaine reste à la
    charge de l'appelant.
    """
    try:
        validated = spec.args_model.model_validate(args)
    except ValidationError as error:
        raise ToolArgumentError(spec.name, error) from error
    # getattr plutôt que model_dump : les arguments gardent leur type converti.
    kwargs = {name: getattr(validated, name) for name in spec.args_model.model_fields}
    return spec.method(**kwargs)
