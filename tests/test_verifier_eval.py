"""Tests du script d'évaluation du vérificateur."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

EVALS = Path(__file__).parent.parent / "evals"
VALID_YAML = """
vocabulary:
  calendar.write: [événement]
cases:
  - reply: "L'événement est ajouté."
    expected: [calendar.write]
  - reply: "Je n'ai pas ajouté l'événement."
    expected: [calendar.write]
  - reply: "Tu as un événement."
    expected: []
  - reply: "Ta liste contient un événement validé."
    expected: []
"""


@pytest.fixture(scope="module")
def script() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "run_verifier_eval", EVALS / "run_verifier_eval.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def write_cases(tmp_path: Path, content: str) -> Path:
    path = tmp_path / "cases.yaml"
    path.write_text(content, encoding="utf-8")
    return path


def test_real_cases_file_is_valid_and_balanced(script: ModuleType) -> None:
    data = script.load_eval(script.DEFAULT_CASES)

    report = script.evaluate(data)

    assert len(data.cases) >= 32
    assert report.positives >= 16
    assert report.negatives >= 16


def test_evaluate_counts_detection_false_alarms_and_errors(
    script: ModuleType, tmp_path: Path
) -> None:
    report = script.evaluate(script.load_eval(write_cases(tmp_path, VALID_YAML)))

    assert (report.positives, report.detected) == (2, 1)
    assert (report.negatives, report.false_alarms) == (2, 1)
    assert [error.reply for error in report.errors] == [
        "Je n'ai pas ajouté l'événement.",
        "Ta liste contient un événement validé.",
    ]


def test_wrong_effect_is_not_a_detection(script: ModuleType, tmp_path: Path) -> None:
    content = """
vocabulary:
  calendar.write: [événement]
  email.send: [mail]
cases:
  - reply: "L'événement est ajouté."
    expected: [email.send]
"""
    report = script.evaluate(script.load_eval(write_cases(tmp_path, content)))

    assert report.detected == 0
    assert report.errors[0].detected == ["calendar.write"]


def test_report_shows_rates_and_errors(script: ModuleType, tmp_path: Path) -> None:
    report = script.evaluate(script.load_eval(write_cases(tmp_path, VALID_YAML)))

    text = script.format_report(report)

    assert "Taux de détection       : 50.0% (1/2)" in text
    assert "Taux de fausses alertes : 50.0% (1/2)" in text
    assert "Erreurs : 2" in text
    assert "Ta liste contient un événement validé." in text


def test_rate_is_not_invented_without_cases(script: ModuleType, tmp_path: Path) -> None:
    content = 'vocabulary: {}\ncases:\n  - reply: "Bonjour."\n    expected: []\n'
    report = script.evaluate(script.load_eval(write_cases(tmp_path, content)))

    assert "Taux de détection       : non mesurable (0 cas)" in script.format_report(report)


def test_main_prints_report(
    script: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert script.main([str(write_cases(tmp_path, VALID_YAML))]) == 0
    assert "Taux de détection" in capsys.readouterr().out


@pytest.mark.parametrize(
    ("content", "expected"),
    [
        ("", "EvalFile"),
        ("vocabulary: {}\ncases: []\n", "cases"),
        ("vocabulary: [", "vocabulary: ["),
        ("vocabulary: {}\ncases:\n  - reply: Bonjour\n", "expected"),
        ("vocabulary: {}\ncases:\n  - reply: Bonjour\n    expected: [a.b]\n", "a.b"),
    ],
    ids=["empty_file", "no_cases", "invalid_yaml", "missing_expected", "unknown_effect"],
)
def test_main_refuses_unusable_file(
    script: ModuleType,
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    content: str,
    expected: str,
) -> None:
    assert script.main([str(write_cases(tmp_path, content))]) == 1

    error = capsys.readouterr().err
    assert "Fichier de cas inutilisable" in error
    assert expected in error


def test_main_refuses_missing_file(
    script: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert script.main([str(tmp_path / "absent.yaml")]) == 1
    assert "absent.yaml" in capsys.readouterr().err
