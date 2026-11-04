"""Configuration lue depuis l'environnement (.env chargé si présent)."""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def _load_dotenv() -> None:
    env = ROOT / ".env"
    if not env.exists():
        return
    for line in env.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


_load_dotenv()


@dataclass
class Settings:
    # LLM via OpenRouter (API compatible OpenAI)
    openrouter_api_key: str = field(default_factory=lambda: os.getenv("OPENROUTER_API_KEY", ""))
    openrouter_url: str = field(
        default_factory=lambda: os.getenv("OPENROUTER_URL", "https://openrouter.ai/api/v1/chat/completions")
    )
    # Deux niveaux de modèle : le routeur choisit lequel réveiller.
    model_fast: str = field(default_factory=lambda: os.getenv("MODEL_FAST", "deepseek/deepseek-chat"))
    model_main: str = field(default_factory=lambda: os.getenv("MODEL_MAIN", "deepseek/deepseek-chat"))
    model_deep: str = field(default_factory=lambda: os.getenv("MODEL_DEEP", "deepseek/deepseek-chat"))

    # Stockage : DATABASE_URL Postgres (Supabase) sinon SQLite local
    database_url: str = field(default_factory=lambda: os.getenv("DATABASE_URL", ""))
    sqlite_path: str = field(default_factory=lambda: os.getenv("SQLITE_PATH", str(ROOT / "data" / "atlas.db")))
    notes_dir: str = field(default_factory=lambda: os.getenv("NOTES_DIR", str(ROOT / "data" / "notes")))

    # Embeddings : endpoint compatible OpenAI optionnel, sinon embedder local
    embeddings_url: str = field(default_factory=lambda: os.getenv("EMBEDDINGS_URL", ""))
    embeddings_key: str = field(default_factory=lambda: os.getenv("EMBEDDINGS_KEY", ""))
    embeddings_model: str = field(default_factory=lambda: os.getenv("EMBEDDINGS_MODEL", ""))

    timezone: str = field(default_factory=lambda: os.getenv("ATLAS_TZ", "Europe/Paris"))
    history_turns: int = 8
    max_tool_rounds: int = 6

    @property
    def use_postgres(self) -> bool:
        return self.database_url.startswith(("postgres://", "postgresql://"))


settings = Settings()
