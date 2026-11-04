"""Client HTTP pour les API compatibles OpenAI : nouvelles tentatives, erreurs claires."""

import json
import time
from collections.abc import Callable
from typing import Any

import httpx

from atlas.llm.base import LLMResponse, LLMUnavailable, Message, ToolCall, Usage

MAX_RETRIES = 2
BACKOFF_SECONDS = 1.0
# Statuts pour lesquels une nouvelle tentative a un sens : saturation ou panne passagère.
RETRYABLE = range(500, 600)
DETAIL_LIMIT = 300


def _as_int(value: Any) -> int:
    """Convertit un compteur reçu en entier positif ; toute valeur douteuse vaut 0."""
    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
        return value
    return 0


def _parse_usage(raw: Any, messages: list[Message], content: str | None) -> Usage:
    """Lit la consommation ; sans elle, l'estime pour que le budget ne soit pas contourné."""
    if not isinstance(raw, dict):
        # Environ 4 caractères par token : grossier, mais jamais zéro.
        prompt = len(json.dumps(messages, ensure_ascii=False, default=str)) // 4
        completion = len(content or "") // 4
        return Usage(
            prompt_tokens=prompt,
            completion_tokens=completion,
            total_tokens=prompt + completion,
            estimated=True,
        )
    prompt = _as_int(raw.get("prompt_tokens"))
    completion = _as_int(raw.get("completion_tokens"))
    total = _as_int(raw.get("total_tokens")) or prompt + completion
    cost = raw.get("cost")
    real_cost = (
        float(cost) if isinstance(cost, int | float) and not isinstance(cost, bool) else None
    )
    if real_cost is not None and real_cost < 0:
        real_cost = None
    return Usage(
        prompt_tokens=prompt, completion_tokens=completion, total_tokens=total, cost=real_cost
    )


def _parse_arguments(raw: Any) -> Any:
    """Décode les arguments d'un outil ; un JSON illisible est transmis tel quel."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    if not isinstance(raw, str):
        return raw
    if not raw.strip():
        # Certains modèles envoient une chaîne vide pour un outil sans paramètre.
        return {}
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        return raw
    # Un JSON valide qui n'est pas un objet (liste, nombre) reste illisible pour un outil.
    return parsed if isinstance(parsed, dict) else raw


def _parse_tool_calls(raw_calls: Any) -> list[ToolCall]:
    """Lit les demandes d'outils, dans l'ordre reçu."""
    if not isinstance(raw_calls, list):
        return []
    calls: list[ToolCall] = []
    for position, raw in enumerate(raw_calls, start=1):
        function = raw.get("function") if isinstance(raw, dict) else None
        if not isinstance(function, dict) or not isinstance(function.get("name"), str):
            raise LLMUnavailable("Réponse illisible : une demande d'outil n'a pas de nom.")
        call_id = raw.get("id")
        calls.append(
            ToolCall(
                id=call_id if isinstance(call_id, str) and call_id else f"call_{position}",
                name=function["name"],
                arguments=_parse_arguments(function.get("arguments")),
            )
        )
    return calls


class OpenAICompatibleClient:
    """Appelle POST {base_url}/chat/completions ; les sous-classes fixent les messages d'erreur."""

    provider = "le fournisseur"

    def __init__(
        self,
        *,
        base_url: str,
        model: str,
        timeout: float,
        api_key: str | None = None,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._url = base_url.rstrip("/") + "/chat/completions"
        self.model = model
        self._timeout = timeout
        self._api_key = api_key
        self._transport = transport
        self._sleep = sleep

    def _headers(self) -> dict[str, str]:
        headers = {"Content-Type": "application/json"}
        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"
        return headers

    def _extra_payload(self) -> dict[str, Any]:
        """Paramètres propres à un fournisseur."""
        return {}

    def _explain_status(self, status: int, detail: str) -> str:
        """Message pour un statut sans nouvelle tentative ; les sous-classes le précisent."""
        return f"{self.provider} a refusé la requête (HTTP {status}). {detail}".strip()

    def _unreachable(self) -> str:
        return f"Connexion impossible avec {self.provider}."

    def chat(self, messages: list[Message], tools: list[dict[str, Any]]) -> LLMResponse:
        """Envoie la conversation ; 429 et 5xx sont retentés, tout le reste échoue aussitôt."""
        payload: dict[str, Any] = {"model": self.model, "messages": messages}
        if tools:
            payload["tools"] = tools
        payload.update(self._extra_payload())
        response = self._post_with_retries(payload)
        return self._parse(response, messages)

    def _post_with_retries(self, payload: dict[str, Any]) -> httpx.Response:
        attempts = MAX_RETRIES + 1
        for attempt in range(attempts):
            response = self._post(payload)
            status = response.status_code
            if status < 400:
                return response
            detail = self._error_detail(response)
            retryable = status == 429 or status in RETRYABLE
            if not retryable:
                raise LLMUnavailable(self._explain_status(status, detail))
            if attempt < attempts - 1:
                self._sleep(BACKOFF_SECONDS * 2**attempt)
        reason = "limite de débit atteinte" if status == 429 else "service indisponible"
        raise LLMUnavailable(
            f"{self.provider} : {reason} (HTTP {status}) après {attempts} tentatives. {detail}"
        )

    def _post(self, payload: dict[str, Any]) -> httpx.Response:
        try:
            with httpx.Client(timeout=self._timeout, transport=self._transport) as client:
                return client.post(self._url, headers=self._headers(), json=payload)
        except httpx.TimeoutException as error:
            raise LLMUnavailable(
                f"{self.provider} n'a pas répondu en {self._timeout:g} s (délai dépassé)."
            ) from error
        except httpx.HTTPError as error:
            raise LLMUnavailable(f"{self._unreachable()} ({type(error).__name__})") from error

    @staticmethod
    def _error_detail(response: httpx.Response) -> str:
        """Extrait le message d'erreur du fournisseur, tronqué."""
        try:
            body = response.json()
        except ValueError:
            return ""
        error = body.get("error") if isinstance(body, dict) else None
        message = error.get("message") if isinstance(error, dict) else error
        if not isinstance(message, str):
            return ""
        return f"Détail : {message[:DETAIL_LIMIT]}"

    def _parse(self, response: httpx.Response, messages: list[Message]) -> LLMResponse:
        try:
            body = response.json()
            if isinstance(body, dict) and "error" in body and "choices" not in body:
                # Certains fournisseurs signalent une erreur avec un statut 200.
                raise LLMUnavailable(
                    f"{self.provider} a renvoyé une erreur. {self._error_detail(response)}"
                )
            message = body["choices"][0]["message"]
        except LLMUnavailable:
            raise
        except (ValueError, KeyError, IndexError, TypeError) as error:
            raise LLMUnavailable(f"Réponse illisible de {self.provider}.") from error
        content = message.get("content")
        text = content if isinstance(content, str) else None
        model = body.get("model")
        return LLMResponse(
            content=text,
            tool_calls=_parse_tool_calls(message.get("tool_calls")),
            usage=_parse_usage(body.get("usage"), messages, text),
            model=model if isinstance(model, str) else self.model,
        )
