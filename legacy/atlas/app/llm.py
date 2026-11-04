"""Client LLM. Le modèle est une pièce interchangeable : tout passe par `chat()`."""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Callable

import httpx

from .config import settings


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: dict


@dataclass
class LLMResponse:
    content: str | None
    tool_calls: list[ToolCall] = field(default_factory=list)
    usage: dict = field(default_factory=dict)
    model: str = ""

    def as_message(self) -> dict:
        msg: dict = {"role": "assistant", "content": self.content or ""}
        if self.tool_calls:
            msg["tool_calls"] = [
                {"id": tc.id, "type": "function",
                 "function": {"name": tc.name, "arguments": json.dumps(tc.arguments, ensure_ascii=False)}}
                for tc in self.tool_calls
            ]
        return msg


class LLMError(RuntimeError):
    pass


class OpenRouterClient:
    def __init__(self, api_key: str | None = None, url: str | None = None):
        self.api_key = api_key or settings.openrouter_api_key
        self.url = url or settings.openrouter_url
        self.http = httpx.Client(timeout=90)

    def chat(self, model: str, messages: list[dict], tools: list[dict] | None = None,
             json_mode: bool = False) -> LLMResponse:
        if not self.api_key:
            raise LLMError("OPENROUTER_API_KEY manquante (voir .env.example)")
        body: dict = {"model": model, "messages": messages, "usage": {"include": True}}
        if tools:
            body["tools"] = tools
        if json_mode:
            body["response_format"] = {"type": "json_object"}
        r = self.http.post(self.url, json=body, headers={
            "Authorization": f"Bearer {self.api_key}",
            "X-Title": "ATLAS personal assistant",
        })
        if r.status_code >= 400:
            raise LLMError(f"OpenRouter {r.status_code}: {r.text[:300]}")
        data = r.json()
        choice = data["choices"][0]["message"]
        calls = []
        for tc in choice.get("tool_calls") or []:
            try:
                args = json.loads(tc["function"].get("arguments") or "{}")
            except json.JSONDecodeError:
                args = {"_raw": tc["function"].get("arguments")}
            calls.append(ToolCall(tc["id"], tc["function"]["name"], args))
        u = data.get("usage") or {}
        usage = {
            "prompt_tokens": u.get("prompt_tokens", 0),
            "completion_tokens": u.get("completion_tokens", 0),
            "cached_tokens": (u.get("prompt_tokens_details") or {}).get("cached_tokens", 0),
            "cost": u.get("cost", 0.0) or 0.0,
        }
        return LLMResponse(choice.get("content"), calls, usage, data.get("model", model))


class ScriptedLLM:
    """LLM factice pour les tests et la démo hors ligne.

    `script` : liste de LLMResponse servies dans l'ordre, ou fonction (messages, tools) -> LLMResponse.
    """

    def __init__(self, script: list[LLMResponse] | Callable[[list[dict], list[dict] | None], LLMResponse]):
        self.script = script
        self.calls: list[dict] = []

    def chat(self, model: str, messages: list[dict], tools: list[dict] | None = None,
             json_mode: bool = False) -> LLMResponse:
        self.calls.append({"model": model, "messages": [dict(m) for m in messages], "tools": tools})
        if callable(self.script):
            return self.script(messages, tools)
        if not self.script:
            return LLMResponse('{"facts": []}' if json_mode else "OK.")
        return self.script.pop(0)
