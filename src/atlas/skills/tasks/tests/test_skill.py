"""Tests de la compétence tasks, via call() comme en production, sur une vraie base SQLite."""

import sqlite3
from datetime import date
from pathlib import Path
from typing import Any

import pytest

from atlas.config import Settings
from atlas.core.executor import ToolExecutor
from atlas.core.receipts import InMemoryReceiptStore, Receipt, ReceiptStatus
from atlas.core.verifier import verify
from atlas.skills.base import SkillContext, SkillContextError, ToolArgumentError, call
from atlas.skills.loader import SkillRegistry, load_skills
from atlas.skills.tasks.skill import TaskNotFoundError, TasksSkill
from atlas.storage.database import IN_MEMORY, connect
from atlas.storage.migrator import apply_migrations, core_source

SKILL_DIR = Path(__file__).parent.parent


@pytest.fixture
def connection() -> sqlite3.Connection:
    return connect(IN_MEMORY)


@pytest.fixture
def registry(connection: sqlite3.Connection) -> SkillRegistry:
    context = SkillContext(connection, Settings(_env_file=None))
    loaded = load_skills(SKILL_DIR.parent, context)
    assert [e for e in loaded.errors if e.folder == "tasks"] == []
    apply_migrations(connection, [core_source(), *loaded.migration_sources()])
    return loaded


def run(registry: SkillRegistry, tool: str, args: dict[str, Any] | None = None) -> Any:
    spec = next(spec for spec in registry.tools if spec.name == f"tasks_{tool}")
    return call(spec, args or {})


def test_skill_is_loaded_with_its_migrations(registry: SkillRegistry) -> None:
    names = {spec.name: spec.effect for spec in registry.tools if spec.name.startswith("tasks_")}

    assert names == {
        "tasks_add_task": "tasks.write",
        "tasks_list_tasks": None,
        "tasks_complete_task": "tasks.write",
        "tasks_delete_task": "tasks.delete",
    }
    assert [source.name for source in registry.migration_sources()] == ["tasks"]
    assert registry.requires_approval() == {"tasks.delete"}


def test_skill_without_context_fails_at_creation() -> None:
    with pytest.raises(SkillContextError, match="tasks"):
        TasksSkill()


def test_add_task_returns_the_stored_task(registry: SkillRegistry) -> None:
    result = run(registry, "add_task", {"title": "Payer le loyer", "due": "2026-12-05"})

    assert result == {"id": 1, "title": "Payer le loyer", "due": "2026-12-05"}


def test_add_task_without_due_date(registry: SkillRegistry) -> None:
    assert run(registry, "add_task", {"title": "Appeler Sam"})["due"] is None


def test_title_is_trimmed(registry: SkillRegistry) -> None:
    assert run(registry, "add_task", {"title": "  Appeler Sam  "})["title"] == "Appeler Sam"


@pytest.mark.parametrize(
    "title", ["", "   ", "x" * 201, None, 42, ["liste"]], ids=lambda v: repr(v)[:20]
)
def test_invalid_title_is_refused_and_nothing_is_stored(
    registry: SkillRegistry, title: Any
) -> None:
    with pytest.raises(ToolArgumentError):
        run(registry, "add_task", {"title": title})

    assert run(registry, "list_tasks", {"include_done": True}) == []


def test_title_bounds_are_inclusive(registry: SkillRegistry) -> None:
    assert run(registry, "add_task", {"title": "x"})["id"] == 1
    assert len(run(registry, "add_task", {"title": "y" * 200})["title"]) == 200


def test_invalid_date_and_unknown_argument_are_refused(registry: SkillRegistry) -> None:
    with pytest.raises(ToolArgumentError):
        run(registry, "add_task", {"title": "A", "due": "pas une date"})
    with pytest.raises(ToolArgumentError):
        run(registry, "add_task", {"title": "A", "priorite": 1})


def test_title_limits_are_in_the_schema_given_to_the_model(registry: SkillRegistry) -> None:
    spec = next(s for s in registry.tools if s.name == "tasks_add_task")
    title = spec.parameters["properties"]["title"]

    assert (title["minLength"], title["maxLength"]) == (1, 200)


def test_database_also_refuses_an_out_of_range_title(
    registry: SkillRegistry, connection: sqlite3.Connection
) -> None:
    with pytest.raises(sqlite3.IntegrityError):
        connection.execute("INSERT INTO tasks (title, created_at) VALUES ('', 'x')")


def test_list_hides_done_tasks_by_default_and_orders_by_due_date(
    registry: SkillRegistry,
) -> None:
    run(registry, "add_task", {"title": "sans date"})
    run(registry, "add_task", {"title": "tard", "due": "2026-12-31"})
    run(registry, "add_task", {"title": "tôt", "due": "2026-11-05"})
    run(registry, "add_task", {"title": "faite"})
    run(registry, "complete_task", {"task_id": 4})

    pending = run(registry, "list_tasks")
    everything = run(registry, "list_tasks", {"include_done": True})

    assert [t["title"] for t in pending] == ["tôt", "tard", "sans date"]
    assert [t["title"] for t in everything] == ["tôt", "tard", "sans date", "faite"]
    assert everything[-1]["done"] is True


def test_list_on_an_empty_database(registry: SkillRegistry) -> None:
    assert run(registry, "list_tasks") == []


def test_complete_task(registry: SkillRegistry) -> None:
    run(registry, "add_task", {"title": "Payer"})

    result = run(registry, "complete_task", {"task_id": 1})

    assert result == {"id": 1, "title": "Payer", "done": True, "already_done": False}
    assert run(registry, "list_tasks") == []


def test_completing_twice_says_so_instead_of_pretending(registry: SkillRegistry) -> None:
    run(registry, "add_task", {"title": "Payer"})
    run(registry, "complete_task", {"task_id": 1})

    assert run(registry, "complete_task", {"task_id": 1})["already_done"] is True


def test_delete_task(registry: SkillRegistry) -> None:
    run(registry, "add_task", {"title": "Payer"})
    run(registry, "add_task", {"title": "Appeler"})

    result = run(registry, "delete_task", {"task_id": 1})

    assert result == {"id": 1, "title": "Payer"}
    assert [t["title"] for t in run(registry, "list_tasks", {"include_done": True})] == ["Appeler"]


@pytest.mark.parametrize("tool", ["complete_task", "delete_task"])
def test_unknown_task_gives_a_clear_error(registry: SkillRegistry, tool: str) -> None:
    # Le chargeur importe skill.py sous un autre nom de module : on compare le type par son nom.
    with pytest.raises(ValueError, match=r"Tâche n°42 introuvable") as caught:
        run(registry, tool, {"task_id": 42})
    assert type(caught.value).__name__ == TaskNotFoundError.__name__


@pytest.mark.parametrize("task_id", [0, -1, "abc", None, 1.5])
def test_invalid_task_id_is_refused(registry: SkillRegistry, task_id: Any) -> None:
    with pytest.raises(ToolArgumentError):
        run(registry, "delete_task", {"task_id": task_id})


def test_sql_injection_in_a_title_is_stored_verbatim_and_harmless(
    registry: SkillRegistry, connection: sqlite3.Connection
) -> None:
    hostile = "x'); DROP TABLE tasks; DELETE FROM tasks; --"
    run(registry, "add_task", {"title": "Garder"})

    added = run(registry, "add_task", {"title": hostile})

    assert added["title"] == hostile
    titles = [t["title"] for t in run(registry, "list_tasks")]
    assert titles == ["Garder", hostile]  # la table existe, rien n'a été supprimé
    stored = connection.execute("SELECT title FROM tasks WHERE id = ?", (added["id"],)).fetchone()
    assert stored["title"] == hostile
    run(registry, "complete_task", {"task_id": added["id"]})
    run(registry, "delete_task", {"task_id": added["id"]})
    assert [t["title"] for t in run(registry, "list_tasks")] == ["Garder"]


def test_tasks_persist_after_reopening_the_database(tmp_path: Path) -> None:
    path = tmp_path / "data" / "atlas.db"
    settings = Settings(_env_file=None)

    first = connect(path)
    registry = load_skills(SKILL_DIR.parent, SkillContext(first, settings))
    apply_migrations(first, [core_source(), *registry.migration_sources()])
    run(registry, "add_task", {"title": "Durable", "due": date(2026, 12, 5).isoformat()})
    first.close()

    second = connect(path)
    reopened = load_skills(SKILL_DIR.parent, SkillContext(second, settings))
    assert apply_migrations(second, [core_source(), *reopened.migration_sources()]) == []

    assert run(reopened, "list_tasks") == [
        {"id": 1, "title": "Durable", "due": "2026-12-05", "done": False}
    ]


# --- avec l'exécuteur et le vérificateur ---------------------------------------------------


def test_delete_waits_for_approval_and_add_does_not(registry: SkillRegistry) -> None:
    executor = ToolExecutor(registry, InMemoryReceiptStore())

    added = executor.execute("t", "tasks_add_task", {"title": "A"})
    deletion = executor.execute("t", "tasks_delete_task", {"task_id": 1})

    assert added.status is ReceiptStatus.OK
    assert deletion.status is ReceiptStatus.PENDING_APPROVAL
    assert run(registry, "list_tasks")[0]["title"] == "A"  # rien n'a été supprimé


def test_unknown_task_through_the_executor_is_an_error_receipt(registry: SkillRegistry) -> None:
    receipt = ToolExecutor(registry, InMemoryReceiptStore()).execute(
        "t", "tasks_complete_task", {"task_id": 7}
    )

    assert receipt.status is ReceiptStatus.ERROR
    assert "Tâche n°7 introuvable" in receipt.result


def receipt(effect: str) -> Receipt:
    return Receipt(id=1, turn_id="t", tool="x", effect=effect, args={}, status=ReceiptStatus.OK)


@pytest.mark.parametrize(
    ("reply", "proof", "unsupported"),
    [
        ("J'ai ajouté la tâche « Payer ».", "tasks.write", False),
        ("La tâche est ajoutée.", "tasks.delete", True),
        ("La tâche 2 est terminée.", "tasks.write", False),
        ("Tâche supprimée.", "tasks.delete", False),
        ("La tâche a été supprimée.", "tasks.delete", False),
        ("J'ai supprimé la tâche 3.", "tasks.delete", False),
        ("Je supprime la tâche 3.", "tasks.delete", False),
        ("The task was deleted.", "tasks.delete", False),
        ("La tâche 4 a été retirée de ta liste.", "tasks.delete", False),
        ("J'ai retiré la tâche 4.", "tasks.delete", False),
        ("The task was removed.", "tasks.delete", False),
        ("La tâche 4 a été retirée.", "tasks.write", True),
        ("La tâche a été supprimée.", "tasks.write", True),
        ("Ta tâche est supprimée.", None, True),
    ],
)
def test_claims_distinguish_adding_from_deleting(
    registry: SkillRegistry, reply: str, proof: str | None, unsupported: bool
) -> None:
    receipts = [receipt(proof)] if proof else []

    result = verify(reply, receipts, registry.claim_domains())

    assert bool(result.unsupported) is unsupported
    assert result.claims  # la phrase est bien reconnue comme une affirmation
