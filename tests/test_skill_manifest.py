"""Tests du format des effets et du modèle de manifeste."""

from typing import Any

import pytest
from pydantic import ValidationError

from atlas.core.lexicon import VERB_CLASSES
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


def claims(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {"nouns": ["mail"], "actions": {"send": "mail_box2.send_now"}}
    return base | overrides


def test_valid_claims_are_accepted_and_nouns_are_trimmed() -> None:
    loaded = manifest(requires_approval=["calendar.write"], claims=claims(nouns=["mail", " note "]))

    assert loaded.requires_approval == ["calendar.write"]
    assert loaded.claims is not None
    assert loaded.claims.nouns == ["mail", "note"]
    assert loaded.claims.actions == {"send": "mail_box2.send_now"}


def test_claims_are_optional() -> None:
    assert manifest().claims is None


@pytest.mark.parametrize("effect", INVALID_EFFECTS)
def test_manifest_refuses_invalid_effect_in_requires_approval(effect: str) -> None:
    with pytest.raises(ValidationError, match="requires_approval"):
        manifest(requires_approval=[effect])


@pytest.mark.parametrize("effect", INVALID_EFFECTS)
def test_manifest_refuses_invalid_effect_in_claim_actions(effect: str) -> None:
    with pytest.raises(ValidationError, match="claims"):
        manifest(claims=claims(actions={"write": effect}))


@pytest.mark.parametrize("nouns", [[], [""], ["   "]], ids=["empty_list", "empty", "blank"])
def test_manifest_refuses_claims_without_usable_noun(nouns: list[str]) -> None:
    with pytest.raises(ValidationError, match="nouns"):
        manifest(claims=claims(nouns=nouns))


def test_manifest_refuses_claims_without_action() -> None:
    with pytest.raises(ValidationError, match="actions"):
        manifest(claims=claims(actions={}))


@pytest.mark.parametrize("verb_class", ["destroy", "Write", "", "send "])
def test_manifest_refuses_a_verb_class_the_core_does_not_know(verb_class: str) -> None:
    with pytest.raises(ValidationError, match="classes de verbes inconnues"):
        manifest(claims=claims(actions={verb_class: "mail_box2.send_now"}))


@pytest.mark.parametrize("verb_class", sorted(VERB_CLASSES))
def test_every_class_of_the_lexicon_is_accepted(verb_class: str) -> None:
    loaded = manifest(claims=claims(actions={verb_class: "a.b"}))

    assert loaded.claims is not None and list(loaded.claims.actions) == [verb_class]


def test_manifest_refuses_an_unknown_key_in_claims() -> None:
    with pytest.raises(ValidationError, match="extra"):
        manifest(claims=claims(verbs=["ajouté"]))


def test_the_old_effect_to_words_format_is_refused() -> None:
    with pytest.raises(ValidationError, match="claims"):
        manifest(claims={"calendar.write": ["agenda"]})


@pytest.mark.parametrize("effect", INVALID_EFFECTS)
def test_tool_refuses_invalid_effect(effect: str) -> None:
    with pytest.raises(ToolDefinitionError, match="domaine.action"):

        class BadEffectSkill(Skill):
            name = "bad"

            @tool(effect=effect)
            def run(self) -> None:
                """Outil à l'effet mal formé."""
