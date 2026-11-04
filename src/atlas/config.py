"""Configuration d'ATLAS, lue depuis l'environnement et le fichier .env."""

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
