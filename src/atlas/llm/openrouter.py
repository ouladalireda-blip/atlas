"""Client OpenRouter."""

import time
from collections.abc import Callable
from typing import Any

import httpx

from atlas.llm.openai_compat import OpenAICompatibleClient

OPENROUTER_URL = "https://openrouter.ai/api/v1"


class OpenRouterClient(OpenAICompatibleClient):
    """OpenRouter : messages d'erreur précis pour 400, 401, 402, 403 et 404."""

    provider = "OpenRouter"

    def __init__(
        self,
        *,
        api_key: str,
        model: str,
        timeout: float,
        base_url: str = OPENROUTER_URL,
        transport: httpx.BaseTransport | None = None,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        super().__init__(
            base_url=base_url,
            model=model,
            timeout=timeout,
            api_key=api_key,
            transport=transport,
            sleep=sleep,
        )

    def _extra_payload(self) -> dict[str, Any]:
        # Demande le coût réel de chaque réponse dans usage.cost.
        return {"usage": {"include": True}}

    def _explain_status(self, status: int, detail: str) -> str:
        explanations = {
            400: "Requête invalide (400) : modèle, paramètres ou format des messages refusés.",
            401: "Clé API refusée (401) : vérifie ATLAS_OPENROUTER_API_KEY.",
            402: "Crédits insuffisants (402) : recharge ton compte OpenRouter.",
            403: "Accès refusé (403) : la clé n'a pas accès à ce modèle, ou le contenu a été "
            "bloqué par la modération.",
            404: f"Modèle ou fournisseur introuvable (404) : vérifie ATLAS_MODEL ({self.model}).",
        }
        message = explanations.get(status, f"OpenRouter a refusé la requête (HTTP {status}).")
        return f"{message} {detail}".strip()
