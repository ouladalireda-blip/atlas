"""Le modèle d'en-tête de docs/eval-format.md doit rester un fichier d'éval valide."""

import importlib.util
import re
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

ROOT = Path(__file__).parent.parent


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "runner_for_doc", ROOT / "evals" / "run_verifier_eval.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def template() -> str:
    text = (ROOT / "docs" / "eval-format.md").read_text(encoding="utf-8")
    blocks = re.findall(r"```yaml\n(.*?)```", text, flags=re.DOTALL)
    assert blocks, "le document doit contenir l'en-tête modèle"
    return blocks[0]


def test_the_template_is_a_valid_eval_file(runner: ModuleType, tmp_path: Path) -> None:
    path = tmp_path / "modele.yaml"
    path.write_text(template(), encoding="utf-8")

    data = runner.load_eval(path)

    assert set(data.domains) == {"calendar", "email", "tasks"}
    assert data.declared_effects == [
        "calendar.write",
        "email.send",
        "tasks.write",
        "tasks.delete",
    ]
    assert len(data.cases) == 3


def test_the_template_runs_through_the_blind_set_option(
    runner: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    path = tmp_path / "hors-depot.yaml"
    path.write_text(template(), encoding="utf-8")

    assert runner.main(["--test", str(path)]) == 0

    output = capsys.readouterr().out
    assert "== test externe : 3 phrases ==" in output
    assert "Exemple" not in output


def test_the_template_matches_the_domains_of_the_repository_files(runner: ModuleType) -> None:
    """L'en-tête modèle et les jeux du dépôt déclarent les mêmes domaines."""
    from_doc = {
        name: (spec.nouns, spec.actions)
        for name, spec in runner.EvalFile.model_validate(yaml.safe_load(template())).domains.items()
    }
    repo = runner.load_eval(ROOT / "evals" / "verifier_dev.yaml")
    from_repo = {name: (spec.nouns, spec.actions) for name, spec in repo.domains.items()}

    assert from_doc == from_repo
