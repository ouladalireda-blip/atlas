"""Tests du contrat de compétence : marquage, schéma JSON et appel validé."""

from datetime import date
from typing import Any

import pytest
from pydantic import BaseModel

from atlas.skills.base import Skill, ToolArgumentError, ToolDefinitionError, ToolSpec, call, tool


class Place(BaseModel):
    city: str


class AgendaSkill(Skill):
    name = "agenda"

    @tool(effect="calendar.write")
    def add(self, title: str, day: date, reminders: int = 0) -> dict[str, Any]:
        """Ajoute un événement à l'agenda."""
        return {"title": title, "day": day, "reminders": reminders}

    @tool()
    def locate(self, place: Place) -> Place:
        """Retourne un lieu."""
        return place

    @tool()
    def fail(self) -> None:
        """Échoue toujours."""
        raise RuntimeError("panne interne")

    def not_a_tool(self) -> None:
        """Méthode ordinaire, invisible pour le modèle."""

    @property
    def explosive(self) -> str:
        raise AssertionError("tools() ne doit pas évaluer les propriétés")


def agenda_tool(name: str) -> ToolSpec:
    return next(spec for spec in AgendaSkill().tools() if spec.name == name)


def test_tools_returns_only_marked_methods() -> None:
    names = [spec.name for spec in AgendaSkill().tools()]

    assert names == ["agenda_add", "agenda_fail", "agenda_locate"]


def test_tool_keeps_effect_and_does_not_run_the_method() -> None:
    assert agenda_tool("agenda_add").effect == "calendar.write"
    assert agenda_tool("agenda_fail").effect is None


def test_schema_separates_required_and_optional_parameters() -> None:
    parameters = agenda_tool("agenda_add").parameters

    assert parameters["type"] == "object"
    assert parameters["required"] == ["title", "day"]
    assert set(parameters["properties"]) == {"title", "day", "reminders"}
    assert parameters["properties"]["day"]["format"] == "date"
    assert parameters["properties"]["reminders"]["default"] == 0
    assert parameters["additionalProperties"] is False


def test_openai_format_uses_docstring_as_description() -> None:
    spec = agenda_tool("agenda_add")

    assert spec.to_openai() == {
        "type": "function",
        "function": {
            "name": "agenda_add",
            "description": "Ajoute un événement à l'agenda.",
            "parameters": spec.parameters,
        },
    }


def test_call_converts_text_to_date_and_applies_defaults() -> None:
    result = call(agenda_tool("agenda_add"), {"title": "Dentiste", "day": "2026-11-04"})

    assert result == {"title": "Dentiste", "day": date(2026, 11, 4), "reminders": 0}


def test_call_keeps_nested_models_converted() -> None:
    result = call(agenda_tool("agenda_locate"), {"place": {"city": "Paris"}})

    assert result == Place(city="Paris")


@pytest.mark.parametrize(
    ("args", "field"),
    [
        ({"title": "Dentiste", "day": "pas une date"}, "day"),
        ({"title": "Dentiste"}, "day"),
        ({"title": "Dentiste", "day": "2026-11-04", "unknown": 1}, "unknown"),
    ],
)
def test_call_refuses_invalid_arguments(args: dict[str, Any], field: str) -> None:
    with pytest.raises(ToolArgumentError) as caught:
        call(agenda_tool("agenda_add"), args)

    assert "agenda_add" in str(caught.value)
    assert field in str(caught.value)


def test_call_does_not_hide_errors_raised_by_the_tool() -> None:
    with pytest.raises(RuntimeError, match="panne interne"):
        call(agenda_tool("agenda_fail"), {})


def test_bare_decorator_is_refused() -> None:
    with pytest.raises(ToolDefinitionError, match="parenthèses"):

        class BareSkill(Skill):
            name = "bare"

            @tool  # type: ignore[arg-type]
            def run(self) -> None:
                """Outil mal décoré."""


def test_tool_without_docstring_is_refused() -> None:
    class UndocumentedSkill(Skill):
        name = "undocumented"

        @tool()
        def run(self) -> None:
            pass

    with pytest.raises(ToolDefinitionError, match="docstring"):
        UndocumentedSkill().tools()


def test_parameter_without_annotation_is_refused() -> None:
    class UntypedSkill(Skill):
        name = "untyped"

        @tool()
        def run(self, value) -> None:  # type: ignore[no-untyped-def]
            """Outil sans annotation."""

    with pytest.raises(ToolDefinitionError, match="value"):
        UntypedSkill().tools()


def test_variadic_parameters_are_refused() -> None:
    class VariadicSkill(Skill):
        name = "variadic"

        @tool()
        def run(self, *values: int) -> None:
            """Outil variadique."""

    with pytest.raises(ToolDefinitionError, match="values"):
        VariadicSkill().tools()
