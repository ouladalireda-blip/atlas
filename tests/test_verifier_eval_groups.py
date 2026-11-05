"""Tests des groupes du script d'évaluation (base et variantes mesurées séparément)."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

EVALS = Path(__file__).parent.parent / "evals"
YAML = """
domains:
  calendar:
    nouns: [événement]
    actions: {write: calendar.write}
cases:
  - reply: "L'événement est ajouté."
    expected: [calendar.write]
  - reply: "Tu as un événement."
    expected: []
  - reply: "Je n'ai pas ajouté l'événement."
    expected: [calendar.write]
    group: variantes
"""


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "run_verifier_eval_groups", EVALS / "run_verifier_eval.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write(tmp_path: Path, content: str = YAML) -> Path:
    path = tmp_path / "cases.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_cases_without_group_belong_to_base(script: ModuleType, tmp_path: Path) -> None:
    data = script.load_eval(write(tmp_path))

    assert [case.group for case in data.cases] == ["base", "base", "variantes"]
    assert script.groups(data) == ["base", "variantes"]


def test_evaluate_can_measure_one_group_only(script: ModuleType, tmp_path: Path) -> None:
    data = script.load_eval(write(tmp_path))

    base = script.evaluate(data, "base")
    variants = script.evaluate(data, "variantes")

    assert (base.positives, base.detected, base.negatives) == (1, 1, 1)
    assert (variants.positives, variants.detected, variants.negatives) == (1, 0, 0)
    assert script.evaluate(data).positives == 2
    assert script.evaluate(data, "inconnu").positives == 0


def test_main_prints_each_group_then_the_total(
    script: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert script.main([str(write(tmp_path))]) == 0

    output = capsys.readouterr().out
    assert output.index("Groupe « base »") < output.index("Groupe « variantes »")
    assert output.index("Groupe « variantes »") < output.index("== Total ==")


def test_main_prints_a_single_report_without_groups(
    script: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    only_base = YAML.split('  - reply: "Je n')[0]

    script.main([str(write(tmp_path, only_base))])

    assert "Groupe" not in capsys.readouterr().out


def test_empty_group_name_is_refused(script: ModuleType, tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="group"):
        script.load_eval(write(tmp_path, YAML.replace("group: variantes", 'group: ""')))


def test_real_variants_are_all_handled(script: ModuleType) -> None:
    """Garde-fou : les variantes écrites avant les règles 1 à 3 restent toutes correctes."""
    report = script.evaluate(script.load_eval(script.DEFAULT_CASES), "variantes")

    assert report.positives >= 9
    assert report.negatives >= 3
    assert report.errors == []
