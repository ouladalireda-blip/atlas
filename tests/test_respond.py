"""Tests de l'outil respond : validation des numéros de reçus, correction, repli, conformité.

Tous les tests utilisent ScriptedLLM : aucun réseau.
"""

import json
from typing import Any

import pytest
from pydantic import ValidationError

from atlas.core.agent import NON_CONFORMING, SYSTEM_PROMPT, Agent, TurnResult, Verdict
from atlas.core.receipts import InMemoryReceiptStore, ReceiptStatus
from atlas.core.respond import (
    RESPOND_TOOL_NAME,
    RespondArgs,
    parse_respond,
    respond_tool_schema,
    salvage_message,
)
from atlas.core.usage import UsageLog
from atlas.llm.base import LLMResponse, LLMUnavailable
from atlas.llm.scripted import ScriptedLLM, text, tool_calls
from atlas.skills.base import Skill, ToolSpec, tool
from atlas.skills.loader import LoadedSkill, SkillRegistry
from atlas.skills.manifest import SkillManifest

CLAIM = "L'événement est ajouté à ton agenda."
ADD = tool_calls(("agenda_add", {"title": "Dentiste"}))


def respond(message: str, actions: list[Any] | None = None, **extra: Any) -> LLMResponse:
    """Réponse scriptée du modèle : un appel à l'outil respond."""
    arguments: dict[str, Any] = {"message": message, "actions": [] if actions is None else actions}
    return tool_calls((RESPOND_TOOL_NAME, {**arguments, **extra}))


class AgendaSkill(Skill):
    name = "agenda"

    def __init__(self) -> None:
        self.added: list[str] = []

    @tool(effect="calendar.write")
    def add(self, title: str) -> str:
        """Ajoute un événement."""
        self.added.append(title)
        return f"ajouté : {title}"

    @tool(effect="calendar.delete")
    def remove(self, title: str) -> str:
        """Supprime un événement (validation humaine)."""
        return f"supprimé : {title}"

    @tool()
    def boom(self) -> str:
        """Tombe en panne."""
        raise RuntimeError("panne interne")


def registry_for(skill: AgendaSkill) -> SkillRegistry:
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
    return SkillRegistry(skills=[LoadedSkill(manifest, skill, tools)], tools=tools, errors=[])


def make_agent(
    script: list[LLMResponse | Exception], **options: Any
) -> tuple[Agent, ScriptedLLM, AgendaSkill, InMemoryReceiptStore, UsageLog]:
    skill = AgendaSkill()
    llm = ScriptedLLM(script)
    store = InMemoryReceiptStore()
    usage = UsageLog(1_000_000, 100.0)
    agent = Agent(llm, registry_for(skill), store, usage, **options)
    return agent, llm, skill, store, usage


def tool_results(llm: ScriptedLLM, call_index: int) -> list[dict[str, Any]]:
    """Contenus JSON des messages « tool » envoyés au modèle lors d'un appel."""
    return [json.loads(m["content"]) for m in llm.calls[call_index] if m["role"] == "tool"]


# --- respond valide ---------------------------------------------------------------------------


def test_valid_respond_ends_the_turn_with_the_cited_receipts() -> None:
    agent, llm, skill, _, _ = make_agent([ADD, respond(f"{CLAIM} (reçu n°1)", [1])])

    result = agent.run_turn("Ajoute le dentiste.")

    assert result.verdict is Verdict.VERIFIED
    assert result.structured is True
    assert result.actions == [1]
    assert result.warnings == []
    assert result.reply == f"{CLAIM} (reçu n°1)"
    assert skill.added == ["Dentiste"]
    assert [(r.tool, r.status) for r in result.receipts] == [("agenda_add", ReceiptStatus.OK)]
    assert len(llm.calls) == 2


def test_respond_without_action_and_without_claim() -> None:
    agent, _, _, _, _ = make_agent([respond("Bonjour ! Comment puis-je t'aider ?")])

    result = agent.run_turn("Salut")

    assert (result.verdict, result.structured) == (Verdict.NO_CLAIMS, True)
    assert result.actions == [] and result.receipts == []
    assert result.reply == "Bonjour ! Comment puis-je t'aider ?"


def test_respond_creates_no_receipt_and_is_not_an_executed_tool() -> None:
    agent, _, skill, store, _ = make_agent([respond("Salut !")])

    result = agent.run_turn("Salut")

    assert store.for_turn(result.turn_id) == []
    assert skill.added == []


def test_duplicated_numbers_are_cited_once() -> None:
    agent, _, _, _, _ = make_agent([ADD, respond(f"{CLAIM}", [1, 1, 1])])

    result = agent.run_turn("Ajoute")

    assert result.actions == [1]
    assert result.verdict is Verdict.VERIFIED


def test_respond_does_not_count_in_the_tool_round_limit() -> None:
    agent, _, _, _, _ = make_agent([ADD, respond(CLAIM, [1])], max_tool_rounds=1)

    result = agent.run_turn("Ajoute")

    assert result.verdict is Verdict.VERIFIED


# --- numéro inexistant ------------------------------------------------------------------------


def test_unknown_number_is_corrected_once_then_the_action_is_removed_with_a_warning() -> None:
    agent, llm, _, _, _ = make_agent([ADD, respond(CLAIM, [1, 42]), respond(CLAIM, [1, 42])])

    result = agent.run_turn("Ajoute")

    assert result.verdict is Verdict.CORRECTED
    assert result.structured is True
    assert result.actions == [1]
    assert result.reply == CLAIM
    assert len(result.warnings) == 1
    assert "n°42" in result.warnings[0] and "retirés" in result.warnings[0]
    # La correction est passée par la réponse de l'outil respond, avec les reçus du tour.
    refusal = tool_results(llm, 2)[-1]
    assert refusal["status"] == "refused"
    assert "n°42 n'est pas un reçu de ce tour" in refusal["error"]
    assert "reçu n°1" in refusal["error"]


def test_unknown_number_fixed_by_the_model_after_the_correction_has_no_warning() -> None:
    agent, _, _, _, _ = make_agent([ADD, respond(CLAIM, [1, 42]), respond(CLAIM, [1])])

    result = agent.run_turn("Ajoute")

    assert result.verdict is Verdict.CORRECTED
    assert result.actions == [1]
    assert result.warnings == []


def test_number_zero_and_negative_numbers_are_not_receipts() -> None:
    agent, _, _, _, _ = make_agent([respond("Rien.", [0, -3]), respond("Rien.", [0, -3])])

    result = agent.run_turn("Salut")

    assert result.actions == []
    assert "n°0" in result.warnings[0] and "n°-3" in result.warnings[0]


# --- numéro d'un autre tour --------------------------------------------------------------------


def test_number_from_another_turn_is_refused_then_removed() -> None:
    agent, llm, _, _, _ = make_agent(
        [
            ADD,
            respond(CLAIM, [1]),
            respond("Rien de nouveau.", [1]),
            respond("Rien de nouveau.", [1]),
        ]
    )
    first = agent.run_turn("Ajoute le dentiste")

    second = agent.run_turn("Et maintenant ?")

    assert first.actions == [1]
    assert second.actions == []
    assert second.verdict is Verdict.CORRECTED
    assert second.receipts == []
    assert "n°1" in second.warnings[0]
    assert "n'est pas un reçu de ce tour" in tool_results(llm, 3)[-1]["error"]


def test_a_receipt_of_another_turn_cannot_prove_a_claim() -> None:
    agent, _, _, _, _ = make_agent(
        [ADD, respond(CLAIM, [1]), respond(CLAIM, [1]), respond(CLAIM, [1])]
    )
    agent.run_turn("Ajoute le dentiste")

    second = agent.run_turn("Ajoute-le encore")

    assert second.verdict is Verdict.BLOCKED
    assert second.actions == []
    assert second.receipts == []
    assert "Je ne peux pas confirmer" in second.reply


# --- reçu en erreur ---------------------------------------------------------------------------


def test_a_receipt_in_error_cannot_be_cited() -> None:
    boom = tool_calls(("agenda_boom", {}))
    honest = "L'opération a échoué, rien n'a été fait."
    agent, llm, _, _, _ = make_agent([boom, respond(honest, [1]), respond(honest, [1])])

    result = agent.run_turn("Fais un truc")

    assert result.verdict is Verdict.CORRECTED
    assert result.actions == []
    assert "n°1" in result.warnings[0]
    assert "échec (statut error)" in tool_results(llm, 2)[-1]["error"]
    assert [r.status for r in result.receipts] == [ReceiptStatus.ERROR]


def test_a_failed_tool_does_not_prove_the_claim_even_if_cited() -> None:
    boom = tool_calls(("agenda_boom", {}))
    agent, _, _, _, _ = make_agent([boom, respond(CLAIM, [1]), respond(CLAIM, [1])])

    result = agent.run_turn("Fais un truc")

    assert result.verdict is Verdict.BLOCKED
    assert result.structured is True
    assert "Je ne peux pas confirmer" in result.reply


# --- pending_approval et rejected --------------------------------------------------------------


REMOVE = tool_calls(("agenda_remove", {"title": "Dentiste"}))


def test_a_pending_receipt_is_accepted_and_reported_as_pending() -> None:
    message = "La suppression attend ta validation."
    agent, _, _, _, _ = make_agent([REMOVE, respond(message, [1])])

    result = agent.run_turn("Supprime le dentiste")

    assert result.actions == [1]
    assert result.structured is True
    assert result.warnings == []
    assert result.receipts[0].status is ReceiptStatus.PENDING_APPROVAL


def test_a_rejected_receipt_is_accepted_but_proves_nothing() -> None:
    agent, _, _, _, _ = make_agent(
        [REMOVE, respond("Tu as refusé la suppression, rien n'a été fait.", [2])],
        approver=lambda receipt: False,
    )

    refused = agent.run_turn("Supprime le dentiste")

    assert refused.actions == [2]
    assert refused.receipts[-1].status is ReceiptStatus.REJECTED
    assert refused.verdict is Verdict.NO_CLAIMS


def test_claiming_a_deletion_that_was_rejected_is_blocked() -> None:
    lie = "J'ai supprimé l'événement."
    agent, _, _, _, _ = make_agent(
        [REMOVE, respond(lie, [2]), respond(lie, [2])], approver=lambda receipt: False
    )

    result = agent.run_turn("Supprime le dentiste")

    assert result.verdict is Verdict.BLOCKED
    assert "Je ne peux pas confirmer" in result.reply


# --- le détecteur de texte, garde-fou secondaire ------------------------------------------------


def test_lying_message_with_empty_actions_is_corrected_then_blocked() -> None:
    agent, llm, _, _, _ = make_agent([respond(CLAIM, []), respond(CLAIM, [])])

    result = agent.run_turn("Ajoute le dentiste")

    assert result.verdict is Verdict.BLOCKED
    assert result.structured is True
    assert "Je ne peux pas confirmer" in result.reply
    assert CLAIM not in result.reply.replace(f"« {CLAIM} »", "")
    assert result.receipts == []
    refusal = tool_results(llm, 1)[-1]
    assert "aucun reçu d'outil réussi" in refusal["error"]
    assert "actions" in refusal["error"]


def test_message_that_claims_an_action_missing_from_the_list_is_corrected() -> None:
    """Un reçu ok existe dans le tour, mais le modèle ne l'a pas cité : correction, puis OK."""
    agent, llm, _, _, _ = make_agent([ADD, respond(CLAIM, []), respond(CLAIM, [1])])

    result = agent.run_turn("Ajoute le dentiste")

    assert result.verdict is Verdict.CORRECTED
    assert result.actions == [1]
    assert "ajoute le numéro du reçu" in tool_results(llm, 2)[-1]["error"]


def test_message_that_claims_more_than_the_cited_receipts_prove() -> None:
    delete_claim = "J'ai ajouté l'événement et supprimé l'événement."
    agent, _, _, _, _ = make_agent([ADD, respond(delete_claim, [1]), respond(delete_claim, [1])])

    result = agent.run_turn("Ajoute puis supprime")

    assert result.verdict is Verdict.BLOCKED
    assert result.actions == [1]


def test_invalid_numbers_and_unproved_claim_are_reported_in_one_correction() -> None:
    agent, llm, _, _, _ = make_agent([respond(CLAIM, [9]), respond(CLAIM, [9])])

    result = agent.run_turn("Ajoute")

    refusal = tool_results(llm, 1)[-1]["error"]
    assert "n°9" in refusal and "aucun reçu d'outil réussi" in refusal
    assert result.verdict is Verdict.BLOCKED
    assert result.actions == []
    assert "n°9" in result.warnings[0]


# --- repli : texte libre ------------------------------------------------------------------------


def test_free_text_is_the_old_behaviour_and_the_turn_is_marked_non_conforming() -> None:
    agent, _, _, _, _ = make_agent([text("Bonjour ! Comment puis-je t'aider ?")])

    result = agent.run_turn("Salut")

    assert result.structured is False
    assert result.verdict is Verdict.NO_CLAIMS
    assert result.reply == "Bonjour ! Comment puis-je t'aider ?"
    assert result.warnings == [NON_CONFORMING]
    assert result.actions == []


def test_free_text_after_tools_is_checked_against_all_the_receipts_of_the_turn() -> None:
    agent, _, _, _, _ = make_agent([ADD, text(f"{CLAIM} (reçu n°1)")])

    result = agent.run_turn("Ajoute")

    assert result.verdict is Verdict.VERIFIED
    assert result.structured is False
    assert NON_CONFORMING in result.warnings


def test_free_text_lie_is_still_caught_by_the_text_detector() -> None:
    agent, _, _, _, _ = make_agent([text(CLAIM), text(CLAIM)])

    result = agent.run_turn("Ajoute")

    assert result.verdict is Verdict.BLOCKED
    assert result.structured is False
    assert NON_CONFORMING in result.warnings


def test_free_text_after_a_respond_correction_keeps_the_single_correction() -> None:
    agent, _, _, _, _ = make_agent([respond(CLAIM, []), text(CLAIM)])

    result = agent.run_turn("Ajoute")

    assert result.verdict is Verdict.BLOCKED
    assert result.structured is False


def test_empty_free_text_is_an_error() -> None:
    agent, _, _, _, _ = make_agent([text("   ")])

    assert agent.run_turn("Salut").verdict is Verdict.ERROR


# --- appels respond mal formés ------------------------------------------------------------------


def test_respond_called_with_other_tools_is_ignored_with_a_note() -> None:
    both = tool_calls(
        ("agenda_add", {"title": "Dentiste"}),
        (RESPOND_TOOL_NAME, {"message": CLAIM, "actions": [1]}),
    )
    agent, llm, skill, _, _ = make_agent([both, respond(CLAIM, [1])])

    result = agent.run_turn("Ajoute")

    assert skill.added == ["Dentiste"]
    assert result.verdict is Verdict.VERIFIED
    assert result.actions == [1]
    results = tool_results(llm, 1)
    assert results[0]["tool"] == "agenda_add"
    assert results[1]["status"] == "refused" and "seul" in results[1]["error"]


def test_respond_called_twice_in_one_response_is_ignored() -> None:
    twice = tool_calls(
        (RESPOND_TOOL_NAME, {"message": "a", "actions": []}),
        (RESPOND_TOOL_NAME, {"message": "b", "actions": []}),
    )
    agent, llm, _, _, _ = make_agent([twice, respond("Voilà.")])

    result = agent.run_turn("Salut")

    assert result.reply == "Voilà."
    assert [r["status"] for r in tool_results(llm, 1)] == ["refused", "refused"]


@pytest.mark.parametrize(
    "arguments",
    [{}, {"actions": []}, {"message": ""}, {"message": "   ", "actions": []}, {"message": 3}],
)
def test_respond_without_a_usable_message_is_corrected_then_fails(arguments: Any) -> None:
    agent, llm, _, _, _ = make_agent(
        [tool_calls((RESPOND_TOOL_NAME, arguments)), tool_calls((RESPOND_TOOL_NAME, arguments))]
    )

    result = agent.run_turn("Salut")

    assert result.verdict is Verdict.ERROR
    assert result.structured is False
    assert tool_results(llm, 1)[-1]["status"] == "refused"


def test_respond_with_unreadable_arguments_is_corrected_then_fixed() -> None:
    agent, _, _, _, _ = make_agent([tool_calls((RESPOND_TOOL_NAME, "pas du json")), respond("Ok.")])

    result = agent.run_turn("Salut")

    assert (result.verdict, result.structured) == (Verdict.CORRECTED, True)
    assert result.reply == "Ok."


def test_respond_with_bad_action_types_keeps_the_message_but_not_the_actions() -> None:
    bad = {"message": "Bonjour.", "actions": ["1", 2.5]}
    agent, _, _, _, _ = make_agent(
        [tool_calls((RESPOND_TOOL_NAME, bad)), tool_calls((RESPOND_TOOL_NAME, bad))]
    )

    result = agent.run_turn("Salut")

    assert result.reply == "Bonjour."
    assert result.actions == []
    assert result.structured is True
    assert "illisible" in result.warnings[0]


def test_booleans_are_not_receipt_numbers() -> None:
    assert isinstance(parse_respond({"message": "x", "actions": [True]}), str)
    assert isinstance(parse_respond({"message": "x", "actions": [1]}), RespondArgs)


def test_missing_actions_means_no_action() -> None:
    parsed = parse_respond({"message": "  Salut  "})

    assert isinstance(parsed, RespondArgs)
    assert parsed.actions == [] and parsed.message == "Salut"


def test_salvage_message() -> None:
    assert salvage_message({"message": " ok ", "actions": "x"}) == "ok"
    assert salvage_message({"message": ""}) is None
    assert salvage_message("texte") is None


# --- schéma fixe, prompt, collision -------------------------------------------------------------


def test_the_respond_schema_is_fixed_last_and_identical_for_every_call() -> None:
    first, llm_a, _, _, _ = make_agent([respond("a"), respond("b")])
    second, llm_b, _, _, _ = make_agent([respond("c")])

    first.run_turn("un")
    first.run_turn("deux")
    second.run_turn("trois")

    seen = [json.dumps(tools, sort_keys=True) for tools in (*llm_a.tools_seen, *llm_b.tools_seen)]
    assert len(set(seen)) == 1
    names = [t["function"]["name"] for t in llm_a.tools_seen[0]]
    assert names[-1] == RESPOND_TOOL_NAME
    assert names.count(RESPOND_TOOL_NAME) == 1
    assert names[:-1] == ["agenda_add", "agenda_boom", "agenda_remove"]
    assert llm_a.tools_seen[0][-1] == respond_tool_schema()


def test_the_respond_schema_requires_a_message_and_a_list_of_integers() -> None:
    parameters = respond_tool_schema()["function"]["parameters"]

    assert parameters["required"] == ["message", "actions"]
    assert parameters["properties"]["actions"]["items"] == {"type": "integer"}
    assert parameters["properties"]["message"]["minLength"] == 1
    assert parameters["additionalProperties"] is False


def test_the_schema_copy_is_independent() -> None:
    schema = respond_tool_schema()
    schema["function"]["name"] = "autre"

    assert respond_tool_schema()["function"]["name"] == RESPOND_TOOL_NAME


def test_the_system_prompt_asks_to_always_finish_with_respond() -> None:
    assert "TOUJOURS" in SYSTEM_PROMPT
    assert "respond" in SYSTEM_PROMPT
    assert "receipt_id" in SYSTEM_PROMPT


def test_a_skill_cannot_define_a_tool_named_respond() -> None:
    skill = AgendaSkill()
    registry = registry_for(skill)
    clash = ToolSpec(
        name=RESPOND_TOOL_NAME,
        description="x",
        effect=None,
        parameters={"type": "object", "properties": {}},
        method=lambda: None,
        args_model=registry.tools[0].args_model,
    )
    broken = SkillRegistry(skills=[], tools=[*registry.tools, clash], errors=[])

    with pytest.raises(ValueError, match="respond"):
        Agent(ScriptedLLM([]), broken, InMemoryReceiptStore(), UsageLog(1_000, 1.0))


# --- journal de conformité ----------------------------------------------------------------------


def test_each_turn_is_logged_with_the_way_it_ended() -> None:
    agent, _, _, _, usage = make_agent(
        [
            respond("Bonjour."),
            text("Bonjour en texte libre."),
            LLMUnavailable("panne"),
            respond("Encore."),
        ]
    )

    results = [agent.run_turn(message) for message in ("a", "b", "c", "d")]

    assert [r.structured for r in results] == [True, False, False, True]
    conformity = usage.conformity()
    assert (conformity.respond, conformity.free_text, conformity.error) == (2, 1, 1)
    assert conformity.answered == 3
    assert conformity.rate == pytest.approx(2 / 3)


def test_a_blocked_respond_still_counts_as_conforming() -> None:
    agent, _, _, _, usage = make_agent([respond(CLAIM, []), respond(CLAIM, [])])

    result = agent.run_turn("Ajoute")

    assert result.verdict is Verdict.BLOCKED
    assert usage.conformity().respond == 1


def test_an_empty_respond_counts_as_an_error_not_as_conforming() -> None:
    bad = tool_calls((RESPOND_TOOL_NAME, {}))
    agent, _, _, _, usage = make_agent([bad, bad])

    agent.run_turn("Salut")

    conformity = usage.conformity()
    assert (conformity.respond, conformity.free_text, conformity.error) == (0, 0, 1)
    assert conformity.rate is None


def test_a_turn_without_a_model_answer_is_an_error_outcome() -> None:
    agent, _, _, _, usage = make_agent([LLMUnavailable("coupure")])

    result = agent.run_turn("Salut")

    assert result.verdict is Verdict.ERROR
    assert result.structured is False
    assert usage.conformity().error == 1


def test_history_keeps_the_message_not_the_tool_call() -> None:
    agent, llm, _, _, _ = make_agent([respond("Première réponse."), respond("Seconde.")])

    agent.run_turn("un")
    agent.run_turn("deux")

    roles = [(m["role"], m.get("content")) for m in llm.calls[1] if m["role"] != "system"]
    assert roles == [
        ("user", "un"),
        ("assistant", "Première réponse."),
        ("user", "deux"),
    ]


def test_turn_result_carries_the_new_fields_immutably() -> None:
    agent, _, _, _, _ = make_agent([respond("Salut.")])

    result: TurnResult = agent.run_turn("Salut")

    with pytest.raises(ValidationError):
        result.structured = False  # type: ignore[misc]
