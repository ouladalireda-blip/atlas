"""Tests des clients OpenRouter et Ollama, sans réseau (httpx.MockTransport)."""

import json
from typing import Any

import httpx
import pytest

from atlas.llm.base import LLMClient, LLMUnavailable, ToolCall
from atlas.llm.ollama import OllamaClient
from atlas.llm.openai_compat import OpenAICompatibleClient
from atlas.llm.openrouter import OpenRouterClient

MESSAGES = [{"role": "user", "content": "Bonjour"}]
TOOLS = [{"type": "function", "function": {"name": "clock_now"}}]
SECRET = "sk-or-secret-123"


def completion(
    content: str | None = "Salut",
    tool_calls: list[dict[str, Any]] | None = None,
    usage: dict[str, Any] | None = None,
    **extra: Any,
) -> dict[str, Any]:
    message: dict[str, Any] = {"role": "assistant", "content": content}
    if tool_calls is not None:
        message["tool_calls"] = tool_calls
    body: dict[str, Any] = {"model": "vrai-modele", "choices": [{"message": message}]}
    if usage is not None:
        body["usage"] = usage
    body.update(extra)
    return body


def raw_call(name: str, arguments: Any, call_id: str | None = "c1") -> dict[str, Any]:
    call: dict[str, Any] = {"type": "function", "function": {"name": name, "arguments": arguments}}
    if call_id is not None:
        call["id"] = call_id
    return call


class Server:
    """Faux serveur : rejoue des réponses dans l'ordre et note les requêtes reçues."""

    def __init__(self, *responses: httpx.Response | Exception) -> None:
        self._responses = list(responses)
        self.requests: list[httpx.Request] = []
        self.sleeps: list[float] = []

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        item = self._responses[len(self.requests) - 1]
        if isinstance(item, Exception):
            raise item
        return item

    @property
    def transport(self) -> httpx.MockTransport:
        return httpx.MockTransport(self)

    def payload(self, index: int = 0) -> dict[str, Any]:
        body = json.loads(self.requests[index].content)
        assert isinstance(body, dict)
        return body


def ok(body: dict[str, Any] | None = None) -> httpx.Response:
    return httpx.Response(200, json=body if body is not None else completion())


def failure(status: int, message: str = "détail du serveur") -> httpx.Response:
    return httpx.Response(status, json={"error": {"message": message}})


def openrouter(server: Server) -> OpenRouterClient:
    return OpenRouterClient(
        api_key=SECRET,
        model="deepseek/deepseek-v4.1-flash-1017",
        timeout=5,
        transport=server.transport,
        sleep=server.sleeps.append,
    )


def ollama(server: Server) -> OllamaClient:
    return OllamaClient(
        model="llama3", timeout=5, transport=server.transport, sleep=server.sleeps.append
    )


def test_clients_satisfy_the_llm_protocol() -> None:
    client: LLMClient = openrouter(Server())
    assert client is not None


def test_openrouter_request_format() -> None:
    server = Server(ok())

    openrouter(server).chat(MESSAGES, TOOLS)

    request = server.requests[0]
    assert str(request.url) == "https://openrouter.ai/api/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {SECRET}"
    payload = server.payload()
    assert payload["model"] == "deepseek/deepseek-v4.1-flash-1017"
    assert payload["messages"] == MESSAGES
    assert payload["tools"] == TOOLS
    assert payload["usage"] == {"include": True}


def test_empty_tool_list_is_not_sent() -> None:
    server = Server(ok())

    openrouter(server).chat(MESSAGES, [])

    assert "tools" not in server.payload()


def test_ollama_request_has_no_key_and_no_usage_flag() -> None:
    server = Server(ok())

    ollama(server).chat(MESSAGES, TOOLS)

    request = server.requests[0]
    assert str(request.url) == "http://localhost:11434/v1/chat/completions"
    assert "Authorization" not in request.headers
    assert "usage" not in server.payload()


def test_base_url_trailing_slash_is_tolerated() -> None:
    server = Server(ok())
    client = OllamaClient(
        model="m", timeout=5, base_url="http://hote:1234/v1/", transport=server.transport
    )

    client.chat(MESSAGES, [])

    assert str(server.requests[0].url) == "http://hote:1234/v1/chat/completions"


def test_parses_text_usage_cost_and_model() -> None:
    usage = {"prompt_tokens": 12, "completion_tokens": 8, "total_tokens": 20, "cost": 0.00042}
    server = Server(ok(completion("Salut", usage=usage)))

    response = openrouter(server).chat(MESSAGES, [])

    assert response.content == "Salut"
    assert response.tool_calls == []
    assert response.model == "vrai-modele"
    assert response.usage.total_tokens == 20
    assert response.usage.prompt_tokens == 12
    assert response.usage.cost == 0.00042
    assert response.usage.estimated is False


def test_missing_cost_stays_none() -> None:
    usage = {"prompt_tokens": 1, "completion_tokens": 2, "total_tokens": 3}
    server = Server(ok(completion(usage=usage)))

    assert openrouter(server).chat(MESSAGES, []).usage.cost is None


@pytest.mark.parametrize("bad_cost", ["0.5", -1, True, None])
def test_dubious_cost_is_ignored(bad_cost: Any) -> None:
    usage = {"total_tokens": 3, "cost": bad_cost}
    server = Server(ok(completion(usage=usage)))

    assert openrouter(server).chat(MESSAGES, []).usage.cost is None


def test_missing_usage_is_estimated_so_the_budget_cannot_be_bypassed() -> None:
    server = Server(ok(completion("a" * 400)))

    usage = openrouter(server).chat(MESSAGES, []).usage

    assert usage.estimated is True
    assert usage.total_tokens >= 100
    assert usage.cost is None


def test_multiple_tool_calls_keep_their_order() -> None:
    calls = [
        raw_call("agenda_add", '{"title": "A"}', "c1"),
        raw_call("agenda_add", '{"title": "B"}', "c2"),
        raw_call("clock_now", "{}", "c3"),
    ]
    server = Server(ok(completion(content=None, tool_calls=calls)))

    response = openrouter(server).chat(MESSAGES, TOOLS)

    assert [(c.id, c.name) for c in response.tool_calls] == [
        ("c1", "agenda_add"),
        ("c2", "agenda_add"),
        ("c3", "clock_now"),
    ]
    assert response.tool_calls[0].arguments == {"title": "A"}
    assert response.content is None


def test_unreadable_arguments_are_passed_through_unchanged() -> None:
    calls = [
        raw_call("a", '{"title": "oups'),
        raw_call("b", "[1, 2]"),
        raw_call("c", "42"),
    ]
    server = Server(ok(completion(content=None, tool_calls=calls)))

    response = openrouter(server).chat(MESSAGES, TOOLS)

    assert [c.arguments for c in response.tool_calls] == ['{"title": "oups', "[1, 2]", "42"]


@pytest.mark.parametrize("empty", ["", "  ", None])
def test_empty_arguments_mean_no_arguments(empty: str | None) -> None:
    server = Server(ok(completion(content=None, tool_calls=[raw_call("clock_now", empty)])))

    assert openrouter(server).chat(MESSAGES, TOOLS).tool_calls[0].arguments == {}


def test_missing_call_id_gets_a_fallback() -> None:
    call = raw_call("clock_now", "{}", call_id=None)
    server = Server(ok(completion(content=None, tool_calls=[call])))

    assert openrouter(server).chat(MESSAGES, TOOLS).tool_calls[0].id == "call_1"


def test_tool_call_without_name_is_refused() -> None:
    call = {"id": "c1", "type": "function", "function": {"arguments": "{}"}}
    server = Server(ok(completion(content=None, tool_calls=[call])))

    with pytest.raises(LLMUnavailable, match="sans nom|n'a pas de nom"):
        openrouter(server).chat(MESSAGES, TOOLS)


def test_arguments_as_text_round_trip() -> None:
    assert ToolCall(id="1", name="n", arguments={"a": "é"}).arguments_as_text() == '{"a": "é"}'
    assert ToolCall(id="1", name="n", arguments="brut {").arguments_as_text() == "brut {"


@pytest.mark.parametrize("status", [429, 500, 502, 503])
def test_retryable_status_then_success(status: int) -> None:
    server = Server(failure(status), ok(completion("Enfin")))

    response = openrouter(server).chat(MESSAGES, [])

    assert response.content == "Enfin"
    assert len(server.requests) == 2
    assert server.sleeps == [1.0]


def test_two_retries_with_increasing_wait_then_failure() -> None:
    server = Server(failure(503), failure(503), failure(503))

    with pytest.raises(LLMUnavailable, match=r"indisponible.*503.*3 tentatives"):
        openrouter(server).chat(MESSAGES, [])

    assert len(server.requests) == 3
    assert server.sleeps == [1.0, 2.0]


def test_rate_limit_failure_is_explicit() -> None:
    server = Server(failure(429), failure(429), failure(429))

    with pytest.raises(LLMUnavailable, match=r"limite de débit.*429"):
        openrouter(server).chat(MESSAGES, [])


@pytest.mark.parametrize(
    ("status", "expected"),
    [
        (400, "Requête invalide"),
        (401, "ATLAS_OPENROUTER_API_KEY"),
        (402, "Crédits insuffisants"),
        (403, "Accès refusé"),
        (404, "ATLAS_MODEL"),
    ],
)
def test_openrouter_client_errors_are_not_retried_and_are_specific(
    status: int, expected: str
) -> None:
    server = Server(failure(status))

    with pytest.raises(LLMUnavailable) as caught:
        openrouter(server).chat(MESSAGES, [])

    assert expected in str(caught.value)
    assert f"({status})" in str(caught.value)
    assert "détail du serveur" in str(caught.value)
    assert len(server.requests) == 1
    assert server.sleeps == []


def test_each_openrouter_message_is_distinct() -> None:
    messages = set()
    for status in (400, 401, 402, 403, 404):
        server = Server(failure(status))
        with pytest.raises(LLMUnavailable) as caught:
            openrouter(server).chat(MESSAGES, [])
        messages.add(str(caught.value))

    assert len(messages) == 5


def test_api_key_never_appears_in_errors() -> None:
    server = Server(failure(401))

    with pytest.raises(LLMUnavailable) as caught:
        openrouter(server).chat(MESSAGES, [])

    assert SECRET not in str(caught.value)


def test_other_client_error_is_not_retried() -> None:
    server = Server(failure(422))

    with pytest.raises(LLMUnavailable, match="422"):
        openrouter(server).chat(MESSAGES, [])

    assert len(server.requests) == 1


def test_error_detail_is_truncated() -> None:
    server = Server(failure(400, "x" * 5000))

    with pytest.raises(LLMUnavailable) as caught:
        openrouter(server).chat(MESSAGES, [])

    assert len(str(caught.value)) < 700


def test_error_without_json_body() -> None:
    server = Server(httpx.Response(401, text="<html>refusé</html>"))

    with pytest.raises(LLMUnavailable, match="401"):
        openrouter(server).chat(MESSAGES, [])


def test_ollama_missing_model_message() -> None:
    server = Server(failure(404))

    with pytest.raises(LLMUnavailable, match="ollama pull llama3"):
        ollama(server).chat(MESSAGES, [])


def test_network_error_is_final_and_not_retried() -> None:
    server = Server(httpx.ConnectError("refusé"))

    with pytest.raises(LLMUnavailable, match="Connexion impossible avec OpenRouter"):
        openrouter(server).chat(MESSAGES, [])

    assert len(server.requests) == 1
    assert server.sleeps == []


def test_ollama_unreachable_message() -> None:
    server = Server(httpx.ConnectError("refusé"))

    with pytest.raises(LLMUnavailable, match="ollama serve"):
        ollama(server).chat(MESSAGES, [])


def test_timeout_message() -> None:
    server = Server(httpx.ReadTimeout("lent"))

    with pytest.raises(LLMUnavailable, match=r"n'a pas répondu en 5 s"):
        openrouter(server).chat(MESSAGES, [])


@pytest.mark.parametrize(
    "response",
    [
        httpx.Response(200, text="pas du json"),
        httpx.Response(200, json={"choices": []}),
        httpx.Response(200, json={"choices": [{}]}),
        httpx.Response(200, json=["liste"]),
        httpx.Response(200, json={"error": {"message": "quota"}}),
    ],
)
def test_unreadable_success_response_is_reported(response: httpx.Response) -> None:
    server = Server(response)

    with pytest.raises(LLMUnavailable):
        openrouter(server).chat(MESSAGES, [])


def test_error_inside_a_200_response_shows_its_detail() -> None:
    server = Server(httpx.Response(200, json={"error": {"message": "quota dépassé"}}))

    with pytest.raises(LLMUnavailable, match="quota dépassé"):
        openrouter(server).chat(MESSAGES, [])


def test_non_text_content_is_treated_as_empty() -> None:
    server = Server(ok(completion(content=["morceaux"])))  # type: ignore[arg-type]

    assert openrouter(server).chat(MESSAGES, []).content is None


def test_generic_client_error_message_and_odd_payloads() -> None:
    server = Server(failure(418, "théière"))
    client = OpenAICompatibleClient(
        base_url="http://x/v1", model="m", timeout=1, transport=server.transport
    )

    with pytest.raises(LLMUnavailable, match=r"418.*théière"):
        client.chat(MESSAGES, [])


def test_ollama_other_status_message() -> None:
    server = Server(failure(400))

    with pytest.raises(LLMUnavailable, match=r"Ollama a refusé la requête \(HTTP 400\)"):
        ollama(server).chat(MESSAGES, [])


def test_arguments_already_decoded_or_odd_are_kept() -> None:
    calls = [raw_call("a", {"deja": "objet"}), raw_call("b", ["liste"])]
    server = Server(ok(completion(content=None, tool_calls=calls)))

    response = openrouter(server).chat(MESSAGES, TOOLS)

    assert [c.arguments for c in response.tool_calls] == [{"deja": "objet"}, ["liste"]]


def test_error_with_a_plain_string_or_odd_message_has_no_detail() -> None:
    server = Server(
        httpx.Response(401, json={"error": "chaîne simple"}),
        httpx.Response(401, json={"error": {"message": 42}}),
    )
    client = openrouter(server)

    for _ in range(2):
        with pytest.raises(LLMUnavailable) as caught:
            client.chat(MESSAGES, [])
        assert "Détail" not in str(caught.value) or "chaîne simple" in str(caught.value)
