"""Tests des réglages du modèle et de la fabrique de clients."""

import pytest
from pydantic import ValidationError

from atlas.config import Settings
from atlas.llm.base import LLMConfigError
from atlas.llm.factory import DEFAULT_OPENROUTER_MODEL, build_client
from atlas.llm.ollama import OllamaClient
from atlas.llm.openrouter import OpenRouterClient
from atlas.llm.scripted import ScriptedLLM, ScriptExhausted, text


@pytest.fixture(autouse=True)
def clean_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    """Isole les tests de l'environnement réel de la machine."""
    for name in list(__import__("os").environ):
        if name.startswith("ATLAS_"):
            monkeypatch.delenv(name)


def settings(**values: object) -> Settings:
    return Settings(_env_file=None, **values)  # type: ignore[arg-type]


def test_defaults() -> None:
    config = settings()

    assert config.provider == "openrouter"
    assert config.model is None
    assert config.openrouter_api_key is None
    assert config.timeout == 60
    assert config.max_tool_rounds == 6
    assert config.history_messages == 16
    assert config.address_form == "tu"
    assert config.daily_token_budget > 0
    assert config.daily_cost_budget > 0


def test_reads_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_PROVIDER", "ollama")
    monkeypatch.setenv("ATLAS_MODEL", "llama3")
    monkeypatch.setenv("ATLAS_TIMEOUT", "12.5")
    monkeypatch.setenv("ATLAS_DAILY_COST_BUDGET", "0.25")

    config = settings()

    assert (config.provider, config.model, config.timeout) == ("ollama", "llama3", 12.5)
    assert config.daily_cost_budget == 0.25


def test_api_key_is_hidden_in_repr() -> None:
    config = settings(openrouter_api_key="sk-secret")

    assert "sk-secret" not in repr(config)
    assert "sk-secret" not in str(config)


@pytest.mark.parametrize(
    "invalid",
    [
        {"provider": "inconnu"},
        {"address_form": "toi"},
        {"timeout": 0},
        {"timeout": -1},
        {"max_tool_rounds": 0},
        {"history_messages": -1},
        {"daily_token_budget": 0},
        {"daily_cost_budget": 0},
        {"daily_cost_budget": -5},
    ],
)
def test_invalid_values_are_refused(invalid: dict[str, object]) -> None:
    with pytest.raises(ValidationError):
        settings(**invalid)


def test_openrouter_without_key_is_refused_with_a_clear_message() -> None:
    with pytest.raises(LLMConfigError, match="ATLAS_OPENROUTER_API_KEY"):
        build_client(settings())


@pytest.mark.parametrize("key", ["", "   "])
def test_openrouter_with_blank_key_is_refused(key: str) -> None:
    with pytest.raises(LLMConfigError, match="ATLAS_OPENROUTER_API_KEY"):
        build_client(settings(openrouter_api_key=key))


def test_openrouter_with_key_uses_default_model() -> None:
    client = build_client(settings(openrouter_api_key="sk-x"))

    assert isinstance(client, OpenRouterClient)
    assert client.model == DEFAULT_OPENROUTER_MODEL


def test_openrouter_model_override() -> None:
    client = build_client(settings(openrouter_api_key="sk-x", model="autre/modele"))

    assert isinstance(client, OllamaClient) is False
    assert getattr(client, "model", None) == "autre/modele"


def test_ollama_needs_no_key_but_needs_a_model() -> None:
    with pytest.raises(LLMConfigError, match="ATLAS_MODEL"):
        build_client(settings(provider="ollama"))

    client = build_client(settings(provider="ollama", model="llama3"))

    assert isinstance(client, OllamaClient)


def test_scripted_llm_replays_in_order_and_records_calls() -> None:
    llm = ScriptedLLM([text("un"), text("deux")])

    first = llm.chat([{"role": "user", "content": "a"}], [])
    second = llm.chat([], [])

    assert (first.content, second.content) == ("un", "deux")
    assert llm.calls[0] == [{"role": "user", "content": "a"}]


def test_scripted_llm_raises_scripted_exceptions_and_when_exhausted() -> None:
    llm = ScriptedLLM([ValueError("boum")])

    with pytest.raises(ValueError, match="boum"):
        llm.chat([], [])
    with pytest.raises(ScriptExhausted):
        llm.chat([], [])
