"""Tests du flux d'approbation : exécuteur, agent, puis `atlas chat` avec la compétence tasks."""

import json
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from atlas.core.agent import Agent, Verdict
from atlas.core.executor import ToolExecutor
from atlas.core.receipts import InMemoryReceiptStore, Receipt, ReceiptStatus
from atlas.core.usage import UsageLog
from atlas.interfaces import cli
from atlas.llm.base import LLMClient
from atlas.llm.scripted import ScriptedLLM, text, tool_calls
from atlas.skills.base import Skill, tool
from atlas.skills.loader import LoadedSkill, SkillRegistry
from atlas.skills.manifest import SkillManifest
from atlas.storage.database import connect
from atlas.storage.receipts import SqliteReceiptStore


class VaultSkill(Skill):
    name = "vault"

    def __init__(self) -> None:
        super().__init__()
        self.deleted: list[str] = []

    @tool(effect="vault.delete")
    def erase(self, name: str) -> str:
        """Efface un secret (validation humaine)."""
        if name == "cassé":
            raise RuntimeError("disque plein")
        self.deleted.append(name)
        return f"effacé : {name}"


def make_registry(skill: VaultSkill) -> SkillRegistry:
    manifest = SkillManifest(
        name="vault",
        version="1.0.0",
        description="Test.",
        examples=[],
        requires_approval=["vault.delete"],
        claims={"nouns": ["secret"], "actions": {"delete": "vault.delete"}},
    )
    tools = skill.tools()
    return SkillRegistry(skills=[LoadedSkill(manifest, skill, tools)], tools=tools, errors=[])


def pending_receipt(executor: ToolExecutor, name: str = "clé") -> Receipt:
    return executor.execute("t", "vault_erase", {"name": name})


# --- exécuteur -----------------------------------------------------------------------------


def test_approve_runs_the_tool_and_links_the_new_receipt() -> None:
    skill = VaultSkill()
    store = InMemoryReceiptStore()
    executor = ToolExecutor(make_registry(skill), store)
    pending = pending_receipt(executor)

    final = executor.approve(pending)

    assert skill.deleted == ["clé"]
    assert final.status is ReceiptStatus.OK
    assert final.parent_id == pending.id
    assert final.result == "effacé : clé"
    assert [r.status for r in store.for_turn("t")] == [
        ReceiptStatus.PENDING_APPROVAL,
        ReceiptStatus.OK,
    ]


def test_reject_never_runs_the_tool() -> None:
    skill = VaultSkill()
    executor = ToolExecutor(make_registry(skill), InMemoryReceiptStore())
    pending = pending_receipt(executor)

    final = executor.reject(pending)

    assert skill.deleted == []
    assert final.status is ReceiptStatus.REJECTED
    assert final.parent_id == pending.id


def test_approved_tool_failure_is_an_error_receipt() -> None:
    executor = ToolExecutor(make_registry(VaultSkill()), InMemoryReceiptStore())

    final = executor.approve(pending_receipt(executor, "cassé"))

    assert final.status is ReceiptStatus.ERROR
    assert "disque plein" in final.result
    assert final.parent_id == 1


def test_only_a_pending_receipt_can_be_approved() -> None:
    skill = VaultSkill()
    executor = ToolExecutor(make_registry(skill), InMemoryReceiptStore())
    done = executor.approve(pending_receipt(executor))

    again = executor.approve(done)  # un reçu déjà traité ne rejoue rien

    assert again.status is ReceiptStatus.ERROR
    assert skill.deleted == ["clé"]


def test_approving_a_receipt_of_a_vanished_tool_is_an_error() -> None:
    skill = VaultSkill()
    executor = ToolExecutor(make_registry(skill), InMemoryReceiptStore())
    ghost = pending_receipt(executor).model_copy(update={"tool": "vault_disparu"})

    assert executor.approve(ghost).status is ReceiptStatus.ERROR
    assert skill.deleted == []


# --- agent ---------------------------------------------------------------------------------


def agent_for(
    script: list[Any], skill: VaultSkill, approver: Any
) -> tuple[Agent, ScriptedLLM, InMemoryReceiptStore]:
    llm = ScriptedLLM(script)
    store = InMemoryReceiptStore()
    agent = Agent(llm, make_registry(skill), store, UsageLog(1_000_000, 100.0), approver=approver)
    return agent, llm, store


ERASE = tool_calls(("vault_erase", {"name": "clé"}))


def tool_content(llm: ScriptedLLM, call_index: int = 1) -> dict[str, Any]:
    (message,) = [m for m in llm.calls[call_index] if m["role"] == "tool"]
    return json.loads(message["content"])


def test_approved_action_runs_and_the_model_gets_the_result() -> None:
    skill = VaultSkill()
    asked: list[Receipt] = []

    def approve(receipt: Receipt) -> bool:
        asked.append(receipt)
        return True

    agent, llm, _ = agent_for([ERASE, text("Le secret est effacé (reçu n°2).")], skill, approve)

    result = agent.run_turn("Efface la clé.")

    assert skill.deleted == ["clé"]
    assert [r.status for r in result.receipts] == [ReceiptStatus.PENDING_APPROVAL, ReceiptStatus.OK]
    assert result.receipts[1].parent_id == result.receipts[0].id
    assert asked[0].args == {"name": "clé"}  # l'approbateur voit l'action et ses arguments
    assert tool_content(llm)["status"] == "ok"
    assert result.verdict is Verdict.VERIFIED


def test_rejected_action_is_not_run_and_the_model_is_told() -> None:
    skill = VaultSkill()
    agent, llm, _ = agent_for([ERASE, text("D'accord, je n'efface rien.")], skill, lambda r: False)

    result = agent.run_turn("Efface la clé.")

    assert skill.deleted == []
    assert [r.status for r in result.receipts] == [
        ReceiptStatus.PENDING_APPROVAL,
        ReceiptStatus.REJECTED,
    ]
    seen = tool_content(llm)
    assert seen["status"] == "rejected"
    assert "refusée" in seen["note"]


def test_a_claim_after_a_rejection_is_blocked() -> None:
    lie = "Le secret est effacé."
    agent, _, _ = agent_for([ERASE, text(lie), text(lie)], VaultSkill(), lambda r: False)

    assert agent.run_turn("Efface la clé.").verdict is Verdict.BLOCKED


def test_a_claim_after_an_approval_is_proved() -> None:
    agent, _, _ = agent_for([ERASE, text("Le secret est effacé.")], VaultSkill(), lambda r: True)

    assert agent.run_turn("Efface la clé.").verdict is Verdict.VERIFIED


def test_without_an_approver_the_action_stays_pending() -> None:
    skill = VaultSkill()
    agent, llm, _ = agent_for([ERASE, text("En attente de validation.")], skill, None)

    result = agent.run_turn("Efface la clé.")

    assert skill.deleted == []
    assert [r.status for r in result.receipts] == [ReceiptStatus.PENDING_APPROVAL]
    assert tool_content(llm)["status"] == "pending_approval"


def test_a_failing_approver_means_refusal() -> None:
    def broken(receipt: Receipt) -> bool:
        raise RuntimeError("écran fermé")

    skill = VaultSkill()
    agent, _, _ = agent_for([ERASE, text("Rien n'a été fait.")], skill, broken)

    result = agent.run_turn("Efface la clé.")

    assert skill.deleted == []
    assert result.receipts[-1].status is ReceiptStatus.REJECTED


def test_each_action_of_a_batch_is_asked_separately_and_in_order() -> None:
    skill = VaultSkill()
    answers = iter([True, False])
    seen: list[str] = []

    def approve(receipt: Receipt) -> bool:
        seen.append(receipt.args["name"])
        return next(answers)

    script = [
        tool_calls(("vault_erase", {"name": "a"}), ("vault_erase", {"name": "b"})),
        text("Voilà."),
    ]
    agent, llm, _ = agent_for(script, skill, approve)

    result = agent.run_turn("Efface a et b.")

    assert seen == ["a", "b"]
    assert skill.deleted == ["a"]
    statuses = [r.status for r in result.receipts]
    assert statuses == [
        ReceiptStatus.PENDING_APPROVAL,
        ReceiptStatus.OK,
        ReceiptStatus.PENDING_APPROVAL,
        ReceiptStatus.REJECTED,
    ]
    contents = [json.loads(m["content"]) for m in llm.calls[1] if m["role"] == "tool"]
    assert [c["status"] for c in contents] == ["ok", "rejected"]


# --- atlas chat avec la vraie compétence tasks ---------------------------------------------

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    import os

    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("ATLAS_"):
            monkeypatch.delenv(name)
    yield


def use_llm(monkeypatch: pytest.MonkeyPatch, llm: LLMClient) -> None:
    monkeypatch.setattr(cli, "build_client", lambda settings: llm)


def stored_titles(path: Path) -> list[str]:
    connection = connect(path)
    try:
        rows = connection.execute("SELECT title FROM tasks ORDER BY id").fetchall()
        return [row["title"] for row in rows]
    finally:
        connection.close()


ADD = tool_calls(("tasks_add_task", {"title": "Payer le loyer"}))
DELETE = tool_calls(("tasks_delete_task", {"task_id": 1}))


def chat(monkeypatch: pytest.MonkeyPatch, script: list[Any], keys: str) -> str:
    use_llm(monkeypatch, ScriptedLLM(script))
    result = runner.invoke(cli.app, ["chat"], input=keys)
    assert result.exit_code == 0, result.output
    return result.output


def test_chat_creates_the_database_and_stores_receipts(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chat(monkeypatch, [ADD, text("La tâche est ajoutée (reçu n°1).")], "Ajoute ça\nexit\n")

    path = tmp_path / "data" / "atlas.db"
    assert path.is_file()
    assert stored_titles(path) == ["Payer le loyer"]
    connection = connect(path)
    receipts = SqliteReceiptStore(connection).get(1)
    connection.close()
    assert receipts is not None and receipts.tool == "tasks_add_task"


def test_chat_approval_yes_deletes_the_task(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = [ADD, text("Ajoutée."), DELETE, text("La tâche est supprimée (reçu n°3).")]

    output = chat(monkeypatch, script, "Ajoute\nSupprime la 1\no\nexit\n")

    assert "Approbation requise" in output
    assert "tasks_delete_task" in output
    assert '"task_id": 1' in output
    assert "Approuver ? (o/n)" in output
    assert "pending_approval" in output
    assert "n°3 ← n°2" in output  # le reçu ok est lié au reçu en attente
    assert stored_titles(tmp_path / "data" / "atlas.db") == []


def test_chat_approval_no_keeps_the_task_and_shows_rejected(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = [ADD, text("Ajoutée."), DELETE, text("Je n'ai rien supprimé.")]

    output = chat(monkeypatch, script, "Ajoute\nSupprime la 1\nn\nexit\n")

    assert "rejected" in output
    assert stored_titles(tmp_path / "data" / "atlas.db") == ["Payer le loyer"]


def test_chat_invalid_answer_is_asked_again(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = [ADD, text("Ajoutée."), DELETE, text("Fait.")]

    output = chat(monkeypatch, script, "Ajoute\nSupprime\npeut-être\noui\nexit\n")

    assert "Réponds par o (oui) ou n (non)." in output
    assert stored_titles(tmp_path / "data" / "atlas.db") == []


def test_chat_end_of_input_during_approval_means_no(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    script = [ADD, text("Ajoutée."), DELETE, text("Rien supprimé.")]

    output = chat(monkeypatch, script, "Ajoute\nSupprime la 1\n")

    assert "action refusée" in output
    assert stored_titles(tmp_path / "data" / "atlas.db") == ["Payer le loyer"]


def test_chat_data_persists_between_two_sessions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    chat(monkeypatch, [ADD, text("Ajoutée.")], "Ajoute\nexit\n")

    output = chat(
        monkeypatch,
        [tool_calls(("tasks_list_tasks", {})), text("Tu as une tâche : Payer le loyer.")],
        "Liste\nexit\n",
    )

    assert "tasks_list_tasks" in output
    assert stored_titles(tmp_path / "data" / "atlas.db") == ["Payer le loyer"]
    connection = connect(tmp_path / "data" / "atlas.db")
    ids = [r["id"] for r in connection.execute("SELECT id FROM receipts ORDER BY id")]
    connection.close()
    assert ids == [1, 2]  # les reçus de la première session sont conservés


def test_chat_budget_is_shared_between_sessions(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ATLAS_DAILY_TOKEN_BUDGET", "100")
    chat(monkeypatch, [text("Salut", tokens=100)], "un\nexit\n")

    output = chat(monkeypatch, [text("jamais")], "deux\nexit\n")

    assert "budget quotidien de tokens atteint" in output


def test_chat_database_path_is_configurable(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ATLAS_DATABASE_PATH", str(tmp_path / "ailleurs" / "x.db"))

    chat(monkeypatch, [ADD, text("Ajoutée.")], "Ajoute\nexit\n")

    assert stored_titles(tmp_path / "ailleurs" / "x.db") == ["Payer le loyer"]
    assert not (tmp_path / "data").exists()


def test_chat_refuses_to_start_when_the_database_cannot_be_opened(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("ATLAS_DATABASE_PATH", str(tmp_path))  # un dossier, pas un fichier
    use_llm(monkeypatch, ScriptedLLM([]))

    result = runner.invoke(cli.app, ["chat"], input="exit\n")

    assert result.exit_code == 1
    assert "Base inutilisable" in result.output


def test_chat_refuses_to_start_when_a_migration_fails(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    use_llm(monkeypatch, ScriptedLLM([]))
    runner.invoke(cli.app, ["chat"], input="exit\n")  # crée la base
    connection = connect(tmp_path / "data" / "atlas.db")
    connection.execute("UPDATE schema_migrations SET checksum = 'altéré' WHERE source = 'tasks'")
    connection.close()

    result = runner.invoke(cli.app, ["chat"], input="exit\n")

    assert result.exit_code == 1
    assert "Migrations en échec" in result.output
    assert "modifiée" in result.output  # Rich peut replier la ligne après ce mot


def test_skills_command_lists_tasks_without_creating_a_database(tmp_path: Path) -> None:
    result = runner.invoke(cli.app, ["skills"])

    assert result.exit_code == 0
    assert "tasks_delete_task" in result.output
    assert "tasks.delete" in result.output
    assert not (tmp_path / "data").exists()


def test_skills_command_reports_a_migration_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from atlas.storage.migrator import MigrationError

    def failing(*args: Any, **kwargs: Any) -> list[str]:
        raise MigrationError("migration cassée")

    monkeypatch.setattr(cli, "apply_migrations", failing)

    result = runner.invoke(cli.app, ["skills"])

    assert "Migrations invalides" in result.output
    assert "migration cassée" in result.output
