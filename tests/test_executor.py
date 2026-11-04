"""Tests de l'exécuteur d'outils."""

import logging
from datetime import date
from typing import Any

import pytest

from atlas.core.executor import ToolExecutor
from atlas.core.receipts import InMemoryReceiptStore, Receipt, ReceiptStatus
from atlas.skills.base import Skill, tool
from atlas.skills.loader import LoadedSkill, SkillRegistry
from atlas.skills.manifest import SkillManifest


class AgendaSkill(Skill):
    name = "agenda"

    def __init__(self) -> None:
        self.calls: list[str] = []

    @tool(effect="calendar.write")
    def add(self, title: str, day: date) -> str:
        """Ajoute un événement."""
        self.calls.append(title)
        return f"{title} le {day.isoformat()}"

    @tool(effect="calendar.delete")
    def clear(self) -> None:
        """Vide l'agenda."""
        self.calls.append("clear")

    @tool()
    def crash(self) -> None:
        """Tombe en panne."""
        raise RuntimeError("panne interne")

    @tool()
    def halt(self) -> None:
        """Tente d'arrêter le programme."""
        raise SystemExit(3)

    @tool()
    def interrupted(self) -> None:
        """Simule un Ctrl+C."""
        raise KeyboardInterrupt


def make_registry(skill: AgendaSkill, requires_approval: list[str]) -> SkillRegistry:
    manifest = SkillManifest(
        name="agenda",
        version="1.0.0",
        description="Test.",
        examples=[],
        requires_approval=requires_approval,
    )
    tools = skill.tools()
    return SkillRegistry(skills=[LoadedSkill(manifest, skill, tools)], tools=tools, errors=[])


class BrokenStore:
    def add(self, receipt: Receipt) -> Receipt:
        raise OSError("disque plein")

    def for_turn(self, turn_id: str) -> list[Receipt]:
        return []


@pytest.fixture
def skill() -> AgendaSkill:
    return AgendaSkill()


@pytest.fixture
def store() -> InMemoryReceiptStore:
    return InMemoryReceiptStore()


@pytest.fixture
def executor(skill: AgendaSkill, store: InMemoryReceiptStore) -> ToolExecutor:
    return ToolExecutor(make_registry(skill, ["calendar.delete"]), store)


def test_success_runs_tool_and_records_ok_receipt(
    executor: ToolExecutor, skill: AgendaSkill, store: InMemoryReceiptStore
) -> None:
    args = {"title": "Dentiste", "day": "2026-11-04"}

    receipt = executor.execute("t1", "agenda_add", args)

    assert receipt.status is ReceiptStatus.OK
    assert receipt.effect == "calendar.write"
    assert receipt.args == args
    assert receipt.result == "Dentiste le 2026-11-04"
    assert skill.calls == ["Dentiste"]
    assert store.for_turn("t1") == [receipt]


def test_unknown_tool_gives_error_receipt(
    executor: ToolExecutor, store: InMemoryReceiptStore
) -> None:
    receipt = executor.execute("t1", "agenda_nope", {})

    assert receipt.status is ReceiptStatus.ERROR
    assert receipt.effect is None
    assert "agenda_nope" in receipt.result
    assert store.for_turn("t1") == [receipt]


@pytest.mark.parametrize(
    "args",
    [{"title": "Dentiste", "day": "pas une date"}, ["pas", "un", "dictionnaire"], None],
    ids=["invalid_field", "list", "none"],
)
def test_invalid_arguments_give_error_receipt_without_running_tool(
    executor: ToolExecutor, skill: AgendaSkill, args: Any
) -> None:
    receipt = executor.execute("t1", "agenda_add", args)

    assert receipt.status is ReceiptStatus.ERROR
    assert receipt.effect == "calendar.write"
    assert receipt.args == args
    assert "Arguments invalides" in receipt.result
    assert skill.calls == []


def test_approval_required_gives_pending_receipt_without_running_tool(
    executor: ToolExecutor, skill: AgendaSkill, store: InMemoryReceiptStore
) -> None:
    receipt = executor.execute("t1", "agenda_clear", {})

    assert receipt.status is ReceiptStatus.PENDING_APPROVAL
    assert receipt.effect == "calendar.delete"
    assert skill.calls == []
    assert store.for_turn("t1") == [receipt]


def test_tool_exception_gives_error_receipt(executor: ToolExecutor) -> None:
    receipt = executor.execute("t1", "agenda_crash", {})

    assert receipt.status is ReceiptStatus.ERROR
    assert "RuntimeError" in receipt.result
    assert "panne interne" in receipt.result


def test_system_exit_gives_error_receipt(
    executor: ToolExecutor, store: InMemoryReceiptStore
) -> None:
    receipt = executor.execute("t1", "agenda_halt", {})

    assert receipt.status is ReceiptStatus.ERROR
    assert "SystemExit" in receipt.result
    assert store.for_turn("t1") == [receipt]


def test_keyboard_interrupt_is_not_captured(executor: ToolExecutor) -> None:
    with pytest.raises(KeyboardInterrupt):
        executor.execute("t1", "agenda_interrupted", {})


def test_approval_list_comes_from_the_registry(
    skill: AgendaSkill, store: InMemoryReceiptStore
) -> None:
    executor = ToolExecutor(make_registry(skill, ["calendar.write"]), store)

    receipt = executor.execute("t1", "agenda_add", {"title": "Dentiste", "day": "2026-11-04"})

    assert receipt.status is ReceiptStatus.PENDING_APPROVAL
    assert skill.calls == []


def test_receipts_get_incremental_ids(executor: ToolExecutor) -> None:
    ids = [executor.execute("t1", "agenda_nope", {}).id for _ in range(3)]

    assert ids == [1, 2, 3]


def test_store_failure_never_returns_an_ok_receipt(
    skill: AgendaSkill, caplog: pytest.LogCaptureFixture
) -> None:
    executor = ToolExecutor(make_registry(skill, []), BrokenStore())

    with caplog.at_level(logging.ERROR):
        receipt = executor.execute("t1", "agenda_add", {"title": "Dentiste", "day": "2026-11-04"})

    assert receipt.status is ReceiptStatus.ERROR
    assert receipt.id == 0
    assert "statut ok" in receipt.result
    assert "disque plein" in receipt.result
    assert "agenda_add" in caplog.text


@pytest.mark.parametrize("raw", ['{"title": "oups', "[1, 2]", 42, None])
def test_non_object_arguments_give_an_error_receipt(raw: Any) -> None:
    skill = AgendaSkill()
    executor = ToolExecutor(make_registry(skill, []), InMemoryReceiptStore())

    receipt = executor.execute("t1", "agenda_add", raw)

    assert receipt.status is ReceiptStatus.ERROR
    assert "Arguments invalides" in receipt.result
    assert receipt.args == raw
    assert skill.calls == []
