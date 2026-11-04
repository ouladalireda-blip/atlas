"""Client Ollama, par son endpoint compatible OpenAI : le modèle tourne en local."""

import time
from collections.abc import Callable

import httpx

from atlas.llm.openai_compat import OpenAICompatibleClient

OLLAMA_URL = "http://localhost:11434/v1"


class OllamaClient(OpenAICompatibleClient):
    """Ollama : aucune clé, aucun coût communiqué."""

    provider = "Ollama"

    def __init__(
        self,
        *,
        model: str,
        timeout: float,
        base_url: str = OLLAMA_URL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self._base_url = base_url
        super().__init__(
            base_url=base_url, model=model, timeout=timeout, transport=transport, sleep=sleep
        )

    def _unreachable(self) -> str:
        return f"Ollama ne répond pas sur {self._base_url} : lance `ollama serve`."

    def _explain_status(self, status: int, detail: str) -> str:
        if status == 404:
            message = f"Modèle introuvable (404) : installe-le avec `ollama pull {self.model}`."
        else:
            message = f"Ollama a refusé la requête (HTTP {status})."
        return f"{message} {detail}".strip()
