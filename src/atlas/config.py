"""Configuration d'ATLAS, lue depuis l'environnement et le fichier .env."""

from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from atlas.core.claims.factory import DEFAULT_DETECTOR, detector_names
from atlas.core.claims.fallback import DEFAULT_MAX_TIMEOUTS, DEFAULT_TIMEOUT
from atlas.logging_setup import normalize_level


class Settings(BaseSettings):
    """Réglages de l'application ; chaque champ correspond à une variable ATLAS_*."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        env_prefix="ATLAS_",
        extra="ignore",
    )

    log_level: str = "INFO"
    # Dossier du fichier de journal (atlas.log, rotation simple). Par défaut : « logs » à côté de la
    # base, donc data/logs, ignoré par git.
    log_dir: Path | None = None

    # Base SQLite locale ; le dossier est créé si besoin. Ne jamais la commiter (data/ est ignoré).
    database_path: Path = Path("data/atlas.db")

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

    # Détecteur d'affirmations d'action du vérificateur (voir docs/design/0001).
    claim_detector: str = DEFAULT_DETECTOR
    # Délai maximal d'un appel au détecteur, en secondes ; au-delà, les règles prennent le relais.
    claim_detector_timeout: float = Field(default=DEFAULT_TIMEOUT, gt=0)
    # Dépassements de délai tolérés avant de désactiver le détecteur pour la session.
    claim_detector_max_timeouts: int = Field(default=DEFAULT_MAX_TIMEOUTS, ge=1)

    @field_validator("claim_detector")
    @classmethod
    def check_claim_detector(cls, value: str) -> str:
        """Refuse un détecteur inconnu avec la liste des valeurs acceptées."""
        if value not in detector_names():
            known = ", ".join(detector_names())
            raise ValueError(f"détecteur inconnu {value!r} (valeurs acceptées : {known})")
        return value

    @field_validator("log_level")
    @classmethod
    def check_log_level(cls, value: str) -> str:
        """Accepte DEBUG, INFO, WARNING, ERROR ou CRITICAL (casse indifférente)."""
        return normalize_level(value)

    @property
    def resolved_log_dir(self) -> Path:
        """Dossier du journal : log_dir, sinon le dossier « logs » à côté de la base."""
        return self.log_dir if self.log_dir is not None else self.database_path.parent / "logs"
