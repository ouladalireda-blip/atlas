"""Construction du client de modèle à partir des réglages."""

from atlas.config import Settings
from atlas.llm.base import LLMClient, LLMConfigError
from atlas.llm.ollama import OllamaClient
from atlas.llm.openrouter import OpenRouterClient

DEFAULT_OPENROUTER_MODEL = "deepseek/deepseek-v4.1-flash-1017"


def build_client(settings: Settings) -> LLMClient:
    """Crée le client du fournisseur choisi ; refuse une configuration incomplète."""
    model = (settings.model or "").strip()
    if settings.provider == "openrouter":
        key = (
            settings.openrouter_api_key.get_secret_value().strip()
            if (settings.openrouter_api_key)
            else ""
        )
        if not key:
            raise LLMConfigError(
                "Clé OpenRouter absente : renseigne ATLAS_OPENROUTER_API_KEY dans .env "
                "(ou choisis ATLAS_PROVIDER=ollama pour un modèle local)."
            )
        return OpenRouterClient(
            api_key=key, model=model or DEFAULT_OPENROUTER_MODEL, timeout=settings.timeout
        )
    if not model:
        raise LLMConfigError(
            "Modèle Ollama absent : renseigne ATLAS_MODEL dans .env (par exemple le nom affiché "
            "par `ollama list`)."
        )
    return OllamaClient(model=model, timeout=settings.timeout, base_url=settings.ollama_base_url)
