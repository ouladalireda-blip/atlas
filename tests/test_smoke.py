"""Tests de fumée : le paquet s'importe, la CLI répond et la configuration se charge."""

from importlib.metadata import version
from pathlib import Path

import pytest
from typer.testing import CliRunner

from atlas import __version__
from atlas.config import Settings
from atlas.interfaces.cli import app


def test_version_matches_package_metadata() -> None:
    assert __version__ == "0.1.0"
    assert version("atlas") == __version__


def test_cli_version_command() -> None:
    result = CliRunner().invoke(app, ["version"])

    assert result.exit_code == 0
    assert result.stdout.strip() == __version__


def test_settings_reads_env_file(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_LOG_LEVEL", raising=False)
    env_file = tmp_path / ".env"
    env_file.write_text("ATLAS_LOG_LEVEL=DEBUG\n", encoding="utf-8")

    settings = Settings(_env_file=env_file)  # type: ignore[call-arg]

    assert settings.log_level == "DEBUG"
