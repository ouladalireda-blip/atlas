"""Réglages communs : aucun test n'écrit de journal dans le dépôt ni ne garde de gestionnaire."""

from collections.abc import Iterator
from pathlib import Path

import pytest

from atlas.logging_setup import reset_logging


@pytest.fixture(autouse=True)
def isolated_logging(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> Iterator[None]:
    """Envoie le fichier de journal dans le dossier temporaire du test et nettoie après."""
    monkeypatch.setenv("ATLAS_LOG_DIR", str(tmp_path / "journaux"))
    yield
    reset_logging()
