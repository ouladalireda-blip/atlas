"""Tests de la commande `atlas chat`, avec un modèle simulé."""

from pathlib import Path

import pytest
from rich.console import Console
from typer.testing import CliRunner

from atlas.core.agent import Agent, TurnResult, Verdict
from atlas.core.receipts import InMemoryReceiptStore, Receipt, ReceiptStatus
from atlas.core.usage import UsageLog
from atlas.interfaces import cli
from atlas.llm.base import LLMClient
from atlas.llm.scripted import ScriptedLLM, text, tool_calls
from atlas.skills.loader import SkillRegistry

runner = CliRunner()


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """Aucun .env ni variable ATLAS_* de la machine ne doit influencer le test."""
    monkeypatch.chdir(tmp_path)
    import os

    for name in list(os.environ):
        if name.startswith("ATLAS_"):
            monkeypatch.delenv(name)


def use_llm(monkeypatch: pytest.MonkeyPatch, llm: LLMClient) -> None:
    monkeypatch.setattr(cli, "build_client", lambda settings: llm)


def test_missing_key_refuses_to_start_with_a_clear_message() -> None:
    result = runner.invoke(cli.app, ["chat"], input="exit\n")

    assert result.exit_code == 1
    assert "Configuration invalide" in result.output
    assert "ATLAS_OPENROUTER_API_KEY" in result.output


def test_exit_word_quits(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = ScriptedLLM([])
    use_llm(monkeypatch, llm)

    result = runner.invoke(cli.app, ["chat"], input="exit\n")

    assert result.exit_code == 0
    assert llm.calls == []


def test_end_of_input_quits_cleanly(monkeypatch: pytest.MonkeyPatch) -> None:
    use_llm(monkeypatch, ScriptedLLM([]))

    result = runner.invoke(cli.app, ["chat"], input="")

    assert result.exit_code == 0


def test_blank_lines_are_ignored(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = ScriptedLLM([])
    use_llm(monkeypatch, llm)

    runner.invoke(cli.app, ["chat"], input="\n   \nexit\n")

    assert llm.calls == []


def test_turn_shows_reply_receipts_verdict_tokens_and_cost(monkeypatch: pytest.MonkeyPatch) -> None:
    llm = ScriptedLLM(
        [
            tool_calls(("clock_now", {}), tokens=100, cost=0.0012),
            text("Il est l'heure indiquée par le reçu n°1.", tokens=40, cost=0.0003),
        ]
    )
    use_llm(monkeypatch, llm)

    result = runner.invoke(cli.app, ["chat"], input="Quelle heure est-il ?\nexit\n")

    assert result.exit_code == 0
    output = result.output
    assert "Il est l'heure indiquée par le reçu n°1." in output
    assert "clock_now" in output
    assert "ok" in output
    assert "Verdict : sans affirmation d'action" in output
    assert "Tour : 140 tokens, $0.0015" in output
    assert "Aujourd'hui : 140 tokens, $0.0015" in output


def test_conversation_continues_after_a_model_failure(monkeypatch: pytest.MonkeyPatch) -> None:
    from atlas.llm.base import LLMUnavailable

    use_llm(monkeypatch, ScriptedLLM([LLMUnavailable("Réseau coupé."), text("Me revoilà.")]))

    result = runner.invoke(cli.app, ["chat"], input="un\ndeux\nexit\n")

    assert result.exit_code == 0
    assert "Réseau coupé." in result.output
    assert "Me revoilà." in result.output
    assert "Verdict : erreur" in result.output


def test_reply_is_not_interpreted_as_rich_markup(monkeypatch: pytest.MonkeyPatch) -> None:
    use_llm(monkeypatch, ScriptedLLM([text("Voir [bold]ceci[/bold] et [liste]")]))

    result = runner.invoke(cli.app, ["chat"], input="x\nexit\n")

    assert "[bold]ceci[/bold]" in result.output
    assert "[liste]" in result.output


def test_every_verdict_has_a_label_and_a_style() -> None:
    assert set(cli.VERDICT_STYLES) == set(Verdict)
    assert set(cli.STATUS_STYLES) == set(ReceiptStatus)


def make_result(verdict: Verdict, status: ReceiptStatus = ReceiptStatus.OK) -> TurnResult:
    receipt = Receipt(
        id=3, turn_id="t", tool="agenda_add", effect="calendar.write", args={}, status=status
    )
    return TurnResult(
        turn_id="t", reply="Réponse.", receipts=[receipt], verdict=verdict, tokens=5, cost=None
    )


def render(result: TurnResult) -> str:
    console = Console(force_terminal=True, color_system="standard", width=100, record=True)
    console.print(cli.render_turn(result, UsageLog(100, 1.0)))
    return console.export_text(styles=True)


def test_verdicts_are_colored() -> None:
    assert "\x1b[32mvérifié" in render(make_result(Verdict.VERIFIED))
    assert "\x1b[33mcorrigé" in render(make_result(Verdict.CORRECTED))
    assert "\x1b[1;31mbloqué" in render(make_result(Verdict.BLOCKED))


def test_receipt_status_is_colored_and_unknown_cost_is_shown() -> None:
    output = render(make_result(Verdict.BLOCKED, ReceiptStatus.ERROR))

    assert "\x1b[31merror" in output
    assert "n°3" in output
    assert "agenda_add" in output
    assert "n/a" in output


def test_agent_built_by_chat_uses_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    """Les réglages de la boucle viennent de Settings, pas de valeurs codées en dur."""
    captured: dict[str, int] = {}

    class SpyAgent(Agent):
        def __init__(
            self,
            llm: LLMClient,
            registry: SkillRegistry,
            store: InMemoryReceiptStore,
            usage_log: UsageLog,
            *,
            max_tool_rounds: int,
            history_messages: int,
        ) -> None:
            captured.update(rounds=max_tool_rounds, history=history_messages)
            super().__init__(
                llm,
                registry,
                store,
                usage_log,
                max_tool_rounds=max_tool_rounds,
                history_messages=history_messages,
            )

    use_llm(monkeypatch, ScriptedLLM([]))
    monkeypatch.setattr(cli, "Agent", SpyAgent)
    monkeypatch.setenv("ATLAS_MAX_TOOL_ROUNDS", "3")
    monkeypatch.setenv("ATLAS_HISTORY_MESSAGES", "4")

    runner.invoke(cli.app, ["chat"], input="exit\n")

    assert captured == {"rounds": 3, "history": 4}


def test_loading_errors_are_displayed(monkeypatch: pytest.MonkeyPatch) -> None:
    from atlas.skills.loader import SkillError

    broken = SkillRegistry(
        skills=[], tools=[], errors=[SkillError("cassee", "manifeste [x] invalide")]
    )
    use_llm(monkeypatch, ScriptedLLM([]))
    monkeypatch.setattr(cli, "load_skills", lambda: broken)

    result = runner.invoke(cli.app, ["chat"], input="exit\n")

    assert result.exit_code == 0
    assert "cassee" in result.output
    assert "manifeste [x] invalide" in result.output
