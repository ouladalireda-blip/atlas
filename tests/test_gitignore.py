"""Vérifie que les données personnelles et les secrets ne peuvent pas être commités."""

import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


def is_ignored(path: str) -> bool:
    command = ["git", "check-ignore", "--quiet", "--no-index", path]
    return subprocess.run(command, cwd=ROOT, check=False).returncode == 0


@pytest.mark.parametrize(
    "path",
    [
        "data/test.db",
        "data/sous-dossier/notes.txt",
        ".env",
        "ailleurs/atlas.db",
        "atlas.db-wal",
        "atlas.db-shm",
    ],
)
def test_private_files_are_ignored(path: str) -> None:
    assert is_ignored(path)


def test_env_example_stays_versioned() -> None:
    assert not is_ignored(".env.example")
