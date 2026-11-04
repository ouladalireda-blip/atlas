"""Configuration d'ATLAS, lue depuis l'environnement et le fichier .env."""

from typing import Literal

from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Réglages de l'application ; chaque champ correspond à une variable ATLAS_*."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ATLAS_",
        extra="ignore",
    )

    log_level: str = "INFO"

    # Modèle de langage. La cohérence (clé, modèle) est contrôlée par build_client.
    provider: Literal["openrouter", "ollama"] = "openrouter"
    model: str | None = None
    openrouter_api_key: SecretStr | None = None
    ollama_base_url: str = "http://localhost:11434/v1"
    timeout: float = Field(default=60.0, gt=0)

    # Boucle agent.
    max_tool_rounds: int = Field(default=6, ge=1)
    history_messages: int = Field(default=16, ge=0)
    address_form: Literal["tu", "vous"] = "tu"

    # Budgets quotidiens : un tour est refusé dès qu'un des deux est atteint.
    daily_token_budget: int = Field(default=200_000, gt=0)
    daily_cost_budget: float = Field(default=1.0, gt=0)
