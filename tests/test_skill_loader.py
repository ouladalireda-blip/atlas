"""Tests de la découverte des compétences et de la commande atlas skills."""

from pathlib import Path

import pytest
from typer.testing import CliRunner

from atlas.interfaces import cli
from atlas.skills.base import call
from atlas.skills.loader import load_skills

FIXTURES = Path(__file__).parent / "fixtures" / "skills"


def manifest_text(name: str) -> str:
    return (
        f'name: {name}\nversion: "1.0.0"\ndescription: Test.\nexamples: []\nrequires_approval: []\n'
    )


def skill_code(name: str, method: str = "run") -> str:
    return (
        "from atlas.skills.base import Skill, tool\n"
        "class TestedSkill(Skill):\n"
        f"    name = {name!r}\n"
        "    @tool()\n"
        f"    def {method}(self) -> str:\n"
        '        """Outil de test."""\n'
        '        return "ok"\n'
    )


def write_skill(root: Path, folder: str, manifest: str, code: str) -> None:
    path = root / folder
    path.mkdir()
    (path / "manifest.yaml").write_text(manifest, encoding="utf-8")
    (path / "skill.py").write_text(code, encoding="utf-8")


def test_discovers_clock_skill() -> None:
    registry = load_skills()

    assert registry.errors == []
    assert "clock" in [loaded.manifest.name for loaded in registry.skills]
    assert "clock_now" in [spec.name for spec in registry.tools]


def test_broken_manifest_is_reported_without_blocking_other_skills() -> None:
    registry = load_skills(FIXTURES)

    assert [loaded.manifest.name for loaded in registry.skills] == ["healthy"]
    assert call(registry.tools[0], {}) == "bonjour"
    assert [error.folder for error in registry.errors] == ["broken_manifest"]
    assert "version" in registry.errors[0].message


@pytest.mark.parametrize(
    ("manifest", "code", "expected"),
    [
        (manifest_text("bad"), skill_code("other"), "'other'"),
        (manifest_text("bad"), "raise RuntimeError('boom')\n", "boom"),
        (manifest_text("bad"), "def broken(:\n", "SyntaxError"),
        (manifest_text("bad"), "VALUE = 1\n", "une seule classe"),
        (manifest_text("Bad-Name"), skill_code("Bad-Name"), "name"),
        (manifest_text("bad") + "require_approval: []\n", skill_code("bad"), "require_approval"),
        ("name: [", skill_code("bad"), "ParserError"),
    ],
    ids=[
        "name_mismatch",
        "import_failure",
        "syntax_error",
        "no_skill_class",
        "invalid_name",
        "unknown_manifest_key",
        "invalid_yaml",
    ],
)
def test_broken_skill_is_reported(tmp_path: Path, manifest: str, code: str, expected: str) -> None:
    write_skill(tmp_path, "bad", manifest, code)
    write_skill(tmp_path, "good", manifest_text("good"), skill_code("good"))

    registry = load_skills(tmp_path)

    assert [loaded.manifest.name for loaded in registry.skills] == ["good"]
    assert [error.folder for error in registry.errors] == ["bad"]
    assert expected in registry.errors[0].message


def test_incomplete_folder_is_reported_and_unrelated_folder_ignored(tmp_path: Path) -> None:
    (tmp_path / "incomplete").mkdir()
    (tmp_path / "incomplete" / "manifest.yaml").write_text(manifest_text("x"), encoding="utf-8")
    (tmp_path / "__pycache__").mkdir()

    registry = load_skills(tmp_path)

    assert [error.folder for error in registry.errors] == ["incomplete"]
    assert "skill.py" in registry.errors[0].message


def test_colliding_tool_names_are_refused(tmp_path: Path) -> None:
    write_skill(tmp_path, "a", manifest_text("a"), skill_code("a", method="b_c"))
    write_skill(tmp_path, "a_b", manifest_text("a_b"), skill_code("a_b", method="c"))

    registry = load_skills(tmp_path)

    assert [spec.name for spec in registry.tools] == ["a_b_c"]
    assert [error.folder for error in registry.errors] == ["a_b"]


def test_duplicate_skill_name_is_refused(tmp_path: Path) -> None:
    write_skill(tmp_path, "first", manifest_text("same"), skill_code("same", method="one"))
    write_skill(tmp_path, "second", manifest_text("same"), skill_code("same", method="two"))

    registry = load_skills(tmp_path)

    assert [spec.name for spec in registry.tools] == ["same_one"]
    assert [error.folder for error in registry.errors] == ["second"]


def test_cli_lists_skills() -> None:
    result = CliRunner().invoke(cli.app, ["skills"])

    assert result.exit_code == 0
    assert "clock_now" in result.stdout
    assert "lecture seule" in result.stdout
    assert "Erreurs de chargement" not in result.stdout


def test_cli_shows_skill_without_tools(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    code = "from atlas.skills.base import Skill\nclass EmptySkill(Skill):\n    name = 'empty'\n"
    write_skill(tmp_path, "empty", manifest_text("empty"), code)
    monkeypatch.setattr(cli, "load_skills", lambda: load_skills(tmp_path))

    result = CliRunner().invoke(cli.app, ["skills"])

    assert result.exit_code == 0
    assert "aucun" in result.stdout


def test_cli_shows_loading_errors(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(cli, "load_skills", lambda: load_skills(FIXTURES))

    result = CliRunner().invoke(cli.app, ["skills"])

    assert result.exit_code == 0
    assert "healthy_hello" in result.stdout
    assert "Erreurs de chargement" in result.stdout
    assert "broken_manifest" in result.stdout
    assert "type=missing" in result.stdout
