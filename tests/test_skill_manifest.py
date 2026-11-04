"""Tests du format des effets et du modèle de manifeste."""

from typing import Any

import pytest
from pydantic import ValidationError

from atlas.skills.base import Skill, ToolDefinitionError, tool
from atlas.skills.manifest import SkillManifest

INVALID_EFFECTS = [
    "calendar_write",
    "calendar",
    "Calendar.Write",
    "calendar.",
    ".write",
    "calendar.write.all",
    "calendar-x.write",
    "calendar.write\n",
    "",
]


def manifest(**overrides: Any) -> SkillManifest:
    values: dict[str, Any] = {
        "name": "agenda",
        "version": "1.0.0",
        "description": "Test.",
        "examples": [],
        "requires_approval": [],
    }
    return SkillManifest.model_validate(values | overrides)


def test_valid_effects_are_accepted() -> None:
    loaded = manifest(
        requires_approval=["calendar.write"], claims={"mail_box2.send_now": ["mail", " note "]}
    )

    assert loaded.requires_approval == ["calendar.write"]
    assert loaded.claims == {"mail_box2.send_now": ["mail", "note"]}


def test_claims_are_optional() -> None:
    assert manifest().claims == {}


@pytest.mark.parametrize("effect", INVALID_EFFECTS)
def test_manifest_refuses_invalid_effect_in_requires_approval(effect: str) -> None:
    with pytest.raises(ValidationError, match="requires_approval"):
        manifest(requires_approval=[effect])


@pytest.mark.parametrize("effect", INVALID_EFFECTS)
def test_manifest_refuses_invalid_effect_in_claims(effect: str) -> None:
    with pytest.raises(ValidationError, match="claims"):
        manifest(claims={effect: ["mot"]})


@pytest.mark.parametrize("words", [[], [""], ["   "]], ids=["empty_list", "empty", "blank"])
def test_manifest_refuses_claims_without_usable_word(words: list[str]) -> None:
    with pytest.raises(ValidationError, match="claims"):
        manifest(claims={"calendar.write": words})


@pytest.mark.parametrize("effect", INVALID_EFFECTS)
def test_tool_refuses_invalid_effect(effect: str) -> None:
    with pytest.raises(ToolDefinitionError, match="domaine.action"):

        class BadEffectSkill(Skill):
            name = "bad"

            @tool(effect=effect)
            def run(self) -> None:
                """Outil à l'effet mal formé."""
