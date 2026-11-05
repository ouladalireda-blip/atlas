"""Tests de la trace des tours (agent) et de la commande `atlas tour` (base SQLite)."""

import json
import logging
import os
import sqlite3
from pathlib import Path
from typing import Any

import pytest
from typer.testing import CliRunner

from atlas.core.agent import Agent
from atlas.core.receipts import InMemoryReceiptStore
from atlas.core.trace import EventKind, InMemoryTraceStore, TraceEvent
from atlas.core.usage import UsageLog
from atlas.interfaces import cli
from atlas.llm.base import LLMResponse
from atlas.llm.scripted import ScriptedLLM, text, tool_calls
from atlas.skills.base import Skill, tool
from atlas.skills.loader import LoadedSkill, SkillRegistry
from atlas.skills.manifest import SkillManifest
from atlas.storage.database import IN_MEMORY, connect
from atlas.storage.migrator import apply_migrations, core_source
from atlas.storage.trace import SqliteTraceStore

runner = CliRunner()
# Rich replie les lignes à la largeur du terminal : on l'élargit pour lire les phrases entières.
WIDE = {"COLUMNS": "250"}
CLAIM = "L'événement est ajouté à ton agenda."
USER_MESSAGE = "Mon message privé pour Marcel Dupont"


def respond(message: str, actions: list[Any] | None = None) -> LLMResponse:
    return tool_calls(("respond", {"message": message, "actions": actions or []}))


class AgendaSkill(Skill):
    name = "agenda"

    @tool(effect="calendar.write")
    def add(self, title: str) -> str:
        """Ajoute un événement."""
        return f"ajouté : {title}"

    @tool(effect="calendar.delete")
    def remove(self, title: str) -> str:
        """Supprime un événement (validation humaine)."""
        return f"supprimé : {title}"


def make_agent(
    script: list[LLMResponse | Exception], trace: Any = None, **options: Any
) -> tuple[Agent, InMemoryTraceStore]:
    skill = AgendaSkill()
    manifest = SkillManifest(
        name="agenda",
        version="1.0.0",
        description="Test.",
        examples=[],
        requires_approval=["calendar.delete"],
        claims={
            "nouns": ["événement"],
            "actions": {"write": "calendar.write", "delete": "calendar.delete"},
        },
    )
    tools = skill.tools()
    registry = SkillRegistry(skills=[LoadedSkill(manifest, skill, tools)], tools=tools, errors=[])
    store = trace if trace is not None else InMemoryTraceStore()
    agent = Agent(
        ScriptedLLM(script),
        registry,
        InMemoryReceiptStore(),
        UsageLog(1_000_000, 100.0),
        trace=store,
        **options,
    )
    return agent, store


def kinds(store: InMemoryTraceStore, turn_id: str) -> list[str]:
    return [event.kind.value for event in store.events(turn_id)]


def refusals(store: InMemoryTraceStore, turn_id: str) -> list[dict[str, Any]]:
    return [e.data for e in store.events(turn_id) if e.kind is EventKind.REFUSAL]


ADD = tool_calls(("agenda_add", {"title": "Dentiste"}))


# --- événements de l'agent ---------------------------------------------------------------------


def test_a_clean_turn_is_traced_from_start_to_final() -> None:
    agent, store = make_agent([ADD, respond(CLAIM, [1])])

    result = agent.run_turn(USER_MESSAGE)

    assert kinds(store, result.turn_id) == ["start", "model_call", "receipt", "model_call", "final"]
    events = store.events(result.turn_id)
    assert events[0].data == {"detector": "rules"}
    assert events[1].data["tool_calls"] == ["agenda_add"] and events[1].data["tokens"] == 10
    assert events[2].data == {"id": 1, "tool": "agenda_add", "status": "ok"}
    assert events[3].data["tool_calls"] == ["respond"]
    final = events[4].data
    assert final["verdict"] == "verified" and final["outcome"] == "respond"
    assert final["actions"] == [1] and final["structured"] is True
    assert final["reply_preview"] == CLAIM


def test_the_trace_never_contains_the_user_message() -> None:
    agent, store = make_agent([respond("Bonjour.")])

    result = agent.run_turn(USER_MESSAGE)

    dumped = json.dumps([event.data for event in store.events(result.turn_id)])
    assert "Marcel" not in dumped


def test_invalid_receipt_number_is_traced_with_each_number_and_its_reason() -> None:
    agent, store = make_agent([ADD, respond(CLAIM, [1, 42]), respond(CLAIM, [1, 42])])

    result = agent.run_turn("x")

    first, second = refusals(store, result.turn_id)
    assert first["reason"] == "invalid_receipt_numbers" and first["action"] == "retry"
    assert first["numbers"] == [{"number": 42, "why": "n'est pas un reçu de ce tour"}]
    assert second["action"] == "removed"


def test_an_unproved_claim_is_traced_with_the_sentence_the_effect_and_the_detector() -> None:
    agent, store = make_agent([respond(CLAIM, []), respond(CLAIM, [])])

    result = agent.run_turn("x")

    first, second = refusals(store, result.turn_id)
    assert first["reason"] == "unproved_claims" and first["action"] == "retry"
    (claim,) = first["claims"]
    assert claim["effect"] == "calendar.write" and CLAIM in claim["text"]
    assert claim["declared"] is True
    assert first["detector"] == "rules" and first["cited"] == []
    assert second["action"] == "blocked"
    final = store.events(result.turn_id)[-1].data
    assert final["verdict"] == "blocked"


def test_a_claim_with_a_cited_receipt_that_does_not_prove_it_lists_the_cited_numbers() -> None:
    both = "J'ai ajouté l'événement et supprimé l'événement."
    agent, store = make_agent([ADD, respond(both, [1]), respond(both, [1])])

    result = agent.run_turn("x")

    first = refusals(store, result.turn_id)[0]
    assert first["cited"] == [1]
    assert [claim["effect"] for claim in first["claims"]] == ["calendar.delete"]


def test_free_text_is_traced_as_non_conforming_then_checked_by_the_detector() -> None:
    agent, store = make_agent([text(CLAIM), text(CLAIM)])

    result = agent.run_turn("x")

    reasons = [(r["reason"], r["action"]) for r in refusals(store, result.turn_id)]
    assert reasons == [
        ("non_conforming", "fallback"),
        ("unproved_claims", "retry"),
        ("non_conforming", "fallback"),
        ("unproved_claims", "blocked"),
    ]
    assert refusals(store, result.turn_id)[1]["cited"] is None


def test_clean_free_text_is_traced_as_non_conforming_only() -> None:
    agent, store = make_agent([text("Bonjour !")])

    result = agent.run_turn("x")

    assert [r["reason"] for r in refusals(store, result.turn_id)] == ["non_conforming"]
    final = store.events(result.turn_id)[-1].data
    assert final["outcome"] == "free_text" and final["structured"] is False


def test_respond_with_other_tools_and_bad_arguments_are_traced() -> None:
    both = tool_calls(("agenda_add", {"title": "x"}), ("respond", {"message": "a", "actions": []}))
    bad = tool_calls(("respond", {"actions": []}))
    agent, store = make_agent([both, bad, respond("Ok.")])

    result = agent.run_turn("x")

    reasons = [(r["reason"], r["action"]) for r in refusals(store, result.turn_id)]
    assert reasons == [
        ("respond_with_other_tools", "ignored"),
        ("invalid_respond_arguments", "retry"),
    ]
    assert "message" in refusals(store, result.turn_id)[1]["detail"]


def test_approval_receipts_are_traced_pending_then_decision() -> None:
    remove = tool_calls(("agenda_remove", {"title": "Dentiste"}))
    agent, store = make_agent(
        [remove, respond("Tu as refusé.", [2])], approver=lambda receipt: False
    )

    result = agent.run_turn("x")

    receipts = [e.data for e in store.events(result.turn_id) if e.kind is EventKind.RECEIPT]
    assert [(r["id"], r["status"]) for r in receipts] == [(1, "pending_approval"), (2, "rejected")]


def test_an_error_turn_is_traced_to_its_final_event() -> None:
    from atlas.llm.base import LLMUnavailable

    agent, store = make_agent([LLMUnavailable("coupure")])

    result = agent.run_turn("x")

    assert kinds(store, result.turn_id) == ["start", "final"]
    final = store.events(result.turn_id)[-1].data
    assert final["verdict"] == "error" and final["outcome"] == "error"
    assert "coupure" in final["reply_preview"]


def test_a_failing_trace_never_breaks_the_turn(caplog: pytest.LogCaptureFixture) -> None:
    class BrokenTrace:
        def add(self, event: TraceEvent) -> None:
            raise sqlite3.OperationalError("disque plein")

        def events(self, turn_id: str) -> list[TraceEvent]:
            return []

    agent, _ = make_agent([respond("Bonjour.")], trace=BrokenTrace())

    with caplog.at_level(logging.ERROR):
        result = agent.run_turn("x")

    assert result.reply == "Bonjour."
    assert "non enregistrée" in caplog.text


def test_the_debug_log_summarises_events_without_quoting_messages(
    caplog: pytest.LogCaptureFixture,
) -> None:
    agent, _ = make_agent([respond(CLAIM, []), respond(CLAIM, [])])

    with caplog.at_level(logging.DEBUG, logger="atlas.core.agent"):
        agent.run_turn(USER_MESSAGE)

    assert "unproved_claims -> retry" in caplog.text
    assert "verdict=blocked" in caplog.text
    assert "Marcel" not in caplog.text and "L'événement est ajouté" not in caplog.text


def test_default_agent_keeps_the_trace_in_memory_without_error() -> None:
    skill = AgendaSkill()
    manifest = SkillManifest(
        name="agenda", version="1.0.0", description="Test.", examples=[], requires_approval=[]
    )
    tools = skill.tools()
    registry = SkillRegistry(skills=[LoadedSkill(manifest, skill, tools)], tools=tools, errors=[])
    agent = Agent(
        ScriptedLLM([respond("Salut.")]),
        registry,
        InMemoryReceiptStore(),
        UsageLog(1_000, 1.0),
    )

    assert agent.run_turn("x").reply == "Salut."


# --- SQLite --------------------------------------------------------------------------------


def open_db() -> sqlite3.Connection:
    connection = connect(IN_MEMORY)
    apply_migrations(connection, [core_source()])
    return connection


def event(turn: str, kind: EventKind = EventKind.START, **data: Any) -> TraceEvent:
    return TraceEvent(turn_id=turn, kind=kind, data=data)


def test_sqlite_round_trip_keeps_order_and_data() -> None:
    store = SqliteTraceStore(open_db())
    store.add(event("a"))
    store.add(event("a", EventKind.MODEL_CALL, tokens=5, tool_calls=["x"], accent="é"))
    store.add(event("b"))

    events = store.events("a")

    assert [e.kind for e in events] == [EventKind.START, EventKind.MODEL_CALL]
    assert events[1].data == {"tokens": 5, "tool_calls": ["x"], "accent": "é"}
    assert store.events("absent") == []


def test_turn_numbers_are_the_rank_of_the_start_events() -> None:
    store = SqliteTraceStore(open_db())
    for turn in ("a", "b", "c"):
        store.add(event(turn))
        store.add(event(turn, EventKind.FINAL))

    assert store.count_turns() == 3
    assert [store.turn_id_at(n) for n in (1, 2, 3)] == ["a", "b", "c"]
    assert [store.number_of(t) for t in ("a", "b", "c")] == [1, 2, 3]
    assert store.last_turn_id() == "c"


@pytest.mark.parametrize("number", [0, -1, 4, 100])
def test_unknown_turn_numbers_give_none(number: int) -> None:
    store = SqliteTraceStore(open_db())
    for turn in ("a", "b", "c"):
        store.add(event(turn))

    assert store.turn_id_at(number) is None


def test_an_empty_trace_has_no_turn() -> None:
    store = SqliteTraceStore(open_db())

    assert (store.count_turns(), store.last_turn_id(), store.turn_id_at(1)) == (0, None, None)
    assert store.number_of("a") is None


def test_corrupt_data_fails_loudly() -> None:
    connection = open_db()
    connection.execute(
        "INSERT INTO turn_events VALUES (1, 'a', 'start', '[1, 2]', '2026-11-05T10:00:00+00:00')"
    )

    with pytest.raises(ValueError, match="illisible"):
        SqliteTraceStore(connection).events("a")


def test_an_unknown_kind_in_the_database_fails_loudly() -> None:
    connection = open_db()
    connection.execute(
        "INSERT INTO turn_events VALUES (1, 'a', 'bizarre', '{}', '2026-11-05T10:00:00+00:00')"
    )

    with pytest.raises(ValueError):
        SqliteTraceStore(connection).events("a")


# --- atlas tour ----------------------------------------------------------------------------


@pytest.fixture
def workspace(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Path:
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("ATLAS_"):
            monkeypatch.delenv(name)
    return tmp_path


def chat(monkeypatch: pytest.MonkeyPatch, script: list[Any], user_input: str) -> str:
    monkeypatch.setattr(cli, "build_client", lambda settings: ScriptedLLM(script))
    result = runner.invoke(cli.app, ["chat"], input=user_input, env=WIDE)
    assert result.exit_code == 0
    return result.output


def tour(*arguments: str) -> tuple[int, str]:
    result = runner.invoke(cli.app, ["tour", *arguments], env=WIDE)
    return result.exit_code, result.output


def test_tour_tells_the_whole_turn_from_the_database(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    script = [
        tool_calls(("clock_now", {}), tokens=100, cost=0.0012),
        respond("Il est l'heure du reçu.", [1]),
    ]
    chat(monkeypatch, script, "Quelle heure ?\nexit\n")

    code, output = tour("1")

    assert code == 0
    assert "Tour n°1" in output
    assert "Appel 1 au modèle (scripted) : 100 tokens, $0.0012 · demande : clock_now" in output
    assert "reçu n°1 clock_now (aucun) → ok" in output
    assert "Appel 2 au modèle (scripted) : 10 tokens, n/a · demande : respond" in output
    assert "Verdict final : sans affirmation d'action · terminé par respond (conforme)" in output
    assert "Reçus cités par le modèle : n°1" in output
    assert "Total : 110 tokens, $0.0012" in output
    assert "✗" not in output


def test_tour_shows_an_unproved_claim_with_the_sentence_and_the_detector(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    lie = "La tâche est ajoutée."
    chat(monkeypatch, [respond(lie), respond(lie)], "Ajoute une tâche\nexit\n")

    code, output = tour("--dernier")

    assert code == 0
    assert "✗ Réponse non acceptée : affirmation détectée sans reçu qui la prouve" in output
    assert "correction renvoyée au modèle" in output
    assert "détecteur « rules » : « La tâche est ajoutée. » (effet attendu : tasks.write)" in output
    assert "reçus cités : aucun" in output
    assert "message remplacé par un refus" in output
    assert "Verdict final : bloqué" in output


def test_tour_shows_invalid_receipt_numbers(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat(monkeypatch, [respond("Rien.", [42]), respond("Rien.", [42])], "Salut\nexit\n")

    _, output = tour("1")

    assert "✗ Réponse non acceptée : numéro de reçu invalide → correction renvoyée" in output
    assert "n°42 n'est pas un reçu de ce tour" in output
    assert "numéros retirés, réponse gardée" in output
    assert "Verdict final : corrigé" in output


def test_tour_shows_a_non_conforming_free_text_answer(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat(monkeypatch, [text("Bonjour en texte libre.")], "Salut\nexit\n")

    _, output = tour("1")

    assert "réponse non conforme (texte libre au lieu de l'outil respond)" in output
    assert "gardée, contrôlée par le détecteur de texte seul" in output
    assert "terminé en texte libre (non conforme)" in output
    assert "⚠ Le modèle n'a pas terminé son tour par l'outil respond" in output


def test_tour_numbers_follow_the_order_of_the_turns(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat(monkeypatch, [respond("Un."), respond("Deux."), respond("Trois.")], "a\nb\nc\nexit\n")

    _, second = tour("2")
    _, last = tour("--dernier")

    assert "Tour n°2" in second
    assert "Tour n°3" in last
    assert "Tour n°2" not in last


def test_tour_shows_an_approval_with_both_receipts(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    add = tool_calls(("tasks_add_task", {"title": "Payer"}))
    delete = tool_calls(("tasks_delete_task", {"task_id": 1}))
    chat(
        monkeypatch,
        [add, respond("Ajoutée.", [1]), delete, respond("Supprimée.", [3])],
        "ajoute\nsupprime\no\nexit\n",
    )

    _, output = tour("2")

    assert "pending_approval" in output
    assert "reçu n°3 ← n°2 tasks_delete_task" in output


def test_tour_without_arguments_or_with_both_is_refused(workspace: Path) -> None:
    for arguments in ((), ("1", "--dernier")):
        code, output = tour(*arguments)
        assert code == 1
        assert "numéro de tour, ou --dernier" in output


def test_tour_number_below_one_is_refused_by_the_command_line(workspace: Path) -> None:
    code, _ = tour("0")

    assert code == 2


def test_tour_without_a_database_says_so_and_creates_nothing(workspace: Path) -> None:
    code, output = tour("--dernier")

    assert code == 0
    assert "atlas chat" in output
    assert not (workspace / "data").exists()


def test_tour_with_an_unknown_number_reports_how_many_turns_exist(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    chat(monkeypatch, [respond("Un.")], "a\nexit\n")

    code, output = tour("5")

    assert code == 1
    assert "Pas de tour n°5" in output and "1 tour(s) tracé(s)" in output


def test_tour_on_a_database_without_trace_says_nothing_is_traced(workspace: Path) -> None:
    connection = connect(workspace / "data" / "atlas.db")
    apply_migrations(connection, [core_source()])
    connection.close()

    code, output = tour("--dernier")

    assert code == 1
    assert "aucun tour tracé" in output


def test_tour_of_a_turn_without_final_says_it_was_interrupted() -> None:
    from rich.console import Console

    events = [event("t1"), event("t1", EventKind.MODEL_CALL, model="m", tokens=3, tool_calls=[])]
    console = Console(width=120, record=True)

    console.print(cli.render_tour(1, "t1" * 4, events, [], []))

    text_out = console.export_text()
    assert "Appel 1 au modèle (m) : 3 tokens, n/a · ne renvoie rien" in text_out
    assert "non enregistrée (tour interrompu ?)" in text_out
    assert "Total : 0 tokens, n/a" in text_out


def test_tour_survives_a_receipt_missing_from_the_table() -> None:
    from rich.console import Console

    events = [
        event("t1"),
        event("t1", EventKind.RECEIPT, id=9, tool="x_y", status="ok"),
    ]
    console = Console(width=120, record=True)

    console.print(cli.render_tour(1, "t1", events, [], []))

    assert "reçu n°9 x_y (reçu introuvable) → ok" in console.export_text()


def test_tour_refuses_an_invalid_configuration(
    workspace: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ATLAS_LOG_LEVEL", "bavard")

    code, output = tour("--dernier")

    assert code == 1
    assert "Configuration invalide" in output


def test_refusal_details_cover_generic_and_undeclared_claims() -> None:
    data = {
        "claims": [
            {
                "effect": "*.send",
                "text": "C'est envoyé.",
                "declared": True,
                "any_of": ["a.send", "b.send"],
            },
            {
                "effect": "mail.write",
                "text": "Le mail est rédigé.",
                "declared": False,
                "any_of": [],
            },
            {"effect": None, "text": "C'est fait.", "declared": True, "any_of": []},
        ],
        "detector": "rules",
        "cited": [4],
    }

    lines = cli._refusal_details(data)

    assert "effet attendu : a.send ou b.send" in lines[0]
    assert "reçus cités : n°4" in lines[0]
    assert "effet attendu : mail.write (aucun outil ne la déclare)" in lines[1]
    assert "effet attendu : une action d'écriture" in lines[2]
    assert cli._refusal_details({"detail": "texte"}) == ["texte"]
    assert cli._refusal_details({}) == []
