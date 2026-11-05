"""Tests du runner comparatif : mêmes comptes que run_verifier_eval, Wilson, latence, MLflow.

Aucun test ne lit evals/verifier_test.yaml : les jeux cachés sont simulés par des fichiers
temporaires dont on vérifie qu'aucune phrase ne sort jamais.
"""

import hashlib
import importlib.util
import re
import subprocess
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from atlas.core.claims import Claim, Domains, factory
from atlas.core.claims.rules import RulesDetector

EVALS = Path(__file__).parent.parent / "evals"
SECRET = "Zorglub a planifié le rendez-vous secret de Marcel."
CASES = f"""
domains:
  calendar:
    nouns: [événement, rendez-vous]
    actions: {{write: calendar.write}}
  email:
    nouns: [mail]
    actions: {{send: email.send}}
cases:
  - reply: "L'événement est ajouté."
    expected: [calendar.write]
  - reply: "Le mail est parti."
    expected: [email.send]
  - reply: "Je n'ai pas envoyé le mail."
    expected: []
  - reply: "{SECRET}"
    expected: []
  - reply: "Tu as un événement demain."
    expected: []
"""
ONLY_POSITIVES = """
domains:
  email:
    nouns: [mail]
    actions: {send: email.send}
cases:
  - reply: "Le mail est parti."
    expected: [email.send]
"""


@pytest.fixture(scope="module")
def compare() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "compare_detectors_under_test", EVALS / "compare_detectors.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def runner(compare: ModuleType) -> ModuleType:
    return compare.runner  # type: ignore[no-any-return]


def write(tmp_path: Path, content: str, name: str = "cases.yaml") -> Path:
    path = tmp_path / name
    path.write_text(content, encoding="utf-8")
    return path


class Silent:
    """Détecteur qui ne trouve jamais rien."""

    name = "muet"

    def detect(self, reply: str, domains: Domains) -> list[Claim]:
        return []


class Broken:
    name = "cassé"

    def detect(self, reply: str, domains: Domains) -> list[Claim]:
        raise RuntimeError(f"échec sur {reply}")


class Counting:
    """Détecteur qui compte ses appels."""

    name = "compteur"

    def __init__(self) -> None:
        self.replies: list[str] = []

    def detect(self, reply: str, domains: Domains) -> list[Claim]:
        self.replies.append(reply)
        return []


def run_main(
    compare: ModuleType, capsys: pytest.CaptureFixture[str], *arguments: str
) -> tuple[int, str, str]:
    code = compare.main(["--repeats", "2", "--warmup", "1", *arguments])
    captured = capsys.readouterr()
    return code, captured.out, captured.err


# --- Statistiques ------------------------------------------------------------------------------


def test_wilson_matches_the_values_of_the_design_document(compare: ModuleType) -> None:
    interval = compare.wilson(85, 100)

    assert interval.low == pytest.approx(0.767, abs=0.001)
    assert interval.high == pytest.approx(0.907, abs=0.001)


def test_wilson_edges_stay_inside_zero_and_one(compare: ModuleType) -> None:
    none_found = compare.wilson(0, 100)
    all_found = compare.wilson(100, 100)

    assert none_found.low == 0.0
    assert 0.03 < none_found.high < 0.04
    assert all_found.high == 1.0
    assert 0.96 < all_found.low < 0.97


def test_wilson_without_cases_invents_nothing(compare: ModuleType) -> None:
    assert compare.wilson(0, 0) is None


def test_percentile_uses_the_nearest_rank(compare: ModuleType) -> None:
    values = [float(n) for n in range(1, 11)]

    assert compare.percentile(values, 0.5) == 5.0
    assert compare.percentile(values, 0.95) == 10.0
    assert compare.percentile([7.0], 0.95) == 7.0
    with pytest.raises(ValueError):
        compare.percentile([], 0.5)


# --- Latence -----------------------------------------------------------------------------------


def test_latency_warms_up_then_measures_every_phrase_n_times(compare: ModuleType) -> None:
    detector = Counting()
    inputs: list[tuple[str, Domains]] = [("a", {}), ("b", {}), ("c", {})]

    latency = compare.measure_latency(detector, inputs, warmup_rounds=2, repeats=4)

    # 2 tours de chauffe (non comptés), puis 4 passages complets : 3 phrases x 4 = 12 mesures.
    assert len(detector.replies) == 2 * 3 + 4 * 3
    assert detector.replies[:6] == ["a", "b", "c", "a", "b", "c"]
    assert sorted(detector.replies[6:]) == ["a"] * 4 + ["b"] * 4 + ["c"] * 4
    assert latency.measures == 12
    assert (latency.phrases, latency.repeats, latency.warmup_rounds) == (3, 4, 2)
    assert 0 <= latency.p50_ms <= latency.p95_ms
    assert latency.machine == compare.machine_description()


def test_latency_without_warmup_is_allowed(compare: ModuleType) -> None:
    detector = Counting()

    latency = compare.measure_latency(detector, [("a", {})], warmup_rounds=0, repeats=3)

    assert detector.replies == ["a", "a", "a"]
    assert latency.warmup_rounds == 0


@pytest.mark.parametrize(("warmup", "repeats"), [(-1, 5), (0, 0)])
def test_latency_refuses_an_invalid_protocol(
    compare: ModuleType, warmup: int, repeats: int
) -> None:
    with pytest.raises(ValueError):
        compare.measure_latency(Counting(), [("a", {})], warmup_rounds=warmup, repeats=repeats)


def test_machine_description_names_the_processor_cores_system_and_python(
    compare: ModuleType,
) -> None:
    description = compare.machine_description()

    assert "cœurs logiques" in description
    assert "Python 3." in description
    assert description.count(" ; ") >= 3


def test_latency_without_input_is_refused(compare: ModuleType) -> None:
    with pytest.raises(ValueError):
        compare.measure_latency(Counting(), [], warmup_rounds=1, repeats=1)


def test_latency_does_not_hide_a_detector_failure(compare: ModuleType) -> None:
    with pytest.raises(RuntimeError):
        compare.measure_latency(Broken(), [("a", {})], warmup_rounds=1, repeats=1)


# --- Empreintes --------------------------------------------------------------------------------


def test_dataset_hash_of_one_file_is_its_sha256(compare: ModuleType, tmp_path: Path) -> None:
    path = write(tmp_path, CASES)

    assert compare.dataset_sha256((path,)) == hashlib.sha256(path.read_bytes()).hexdigest()


def test_dataset_hash_of_several_files_depends_on_content_and_order(
    compare: ModuleType, tmp_path: Path
) -> None:
    first = write(tmp_path, CASES, "a.yaml")
    second = write(tmp_path, ONLY_POSITIVES, "b.yaml")

    both = compare.dataset_sha256((first, second))

    assert both == compare.dataset_sha256((first, second))
    assert both != compare.dataset_sha256((second, first))
    second.write_text(ONLY_POSITIVES + "\n# modifié\n", encoding="utf-8")
    assert both != compare.dataset_sha256((first, second))


# --- Non-régression : R via le comparatif = run_verifier_eval ----------------------------------


def counts(text: str) -> list[tuple[int, int]]:
    """Les effectifs (x/n) du bloc agrégé : avant la première ligne vide."""
    block = text.split("\n\n")[0]
    return [(int(x), int(n)) for x, n in re.findall(r"\((\d+)/(\d+)\)", block)]


def test_rules_through_the_comparison_equal_run_verifier_eval_on_development(
    compare: ModuleType, runner: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    config = runner.load_config(runner.DEFAULT_CONFIG)
    base = runner.DEFAULT_CONFIG.parent
    expected = runner.evaluate_set(config, "dev", base)

    dataset = compare.resolve_dataset(compare._parser().parse_args([]))
    measurement = compare.measure(
        RulesDetector(), dataset, compare.load_dataset(dataset), latency=False
    )

    # Mêmes comptes, globaux et par effet, et mêmes erreurs : l'égalité est celle des rapports.
    assert measurement.report == expected
    assert dataset.name == "dev" and not dataset.hidden

    # Mêmes chiffres imprimés, dans le même ordre (global, puis chaque effet).
    assert runner.main([]) == 0
    reference = counts(capsys.readouterr().out)
    code, out, _ = run_main(compare, capsys, "--no-latency")
    printed = counts(out)
    assert code == 0
    assert printed == reference
    assert len(printed) == 2 + 2 * len(expected.effects)


# --- Ligne de commande -------------------------------------------------------------------------


def test_main_reports_rates_intervals_and_latency(
    compare: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = write(tmp_path, CASES)

    code, out, err = run_main(compare, capsys, str(path))

    assert code == 0 and err == ""
    assert "== rules · fichiers · 5 phrases · sha256" in out
    assert re.search(r"détection\s+\d+\.\d% \(2/2\) IC95 \[", out)
    assert "[effectif insuffisant]" in out
    assert "latence par réponse : p50" in out
    assert "(10 mesures = 5 phrases x 2 répétitions, 1 tour(s) de chauffe)" in out
    assert "  machine : " in out


def test_main_can_skip_the_latency(
    compare: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out, _ = run_main(compare, capsys, "--no-latency", str(write(tmp_path, CASES)))

    assert code == 0
    assert "latence : non mesurée" in out


def test_several_detectors_are_measured_on_the_same_set_and_summarised(
    compare: ModuleType,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(factory._BUILDERS, "muet", Silent)
    path = write(tmp_path, CASES)

    code, out, _ = run_main(compare, capsys, "--detectors", "rules,muet", str(path))

    assert code == 0
    assert "== rules · fichiers" in out and "== muet · fichiers" in out
    summary = out.split("\n\n")[-1]
    assert summary.splitlines()[0].startswith("détecteur")
    assert "muet" in summary and "0.0%" in summary


def test_the_hidden_set_prints_aggregates_and_never_a_phrase(
    compare: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    path = write(tmp_path, CASES, "aveugle.yaml")

    code, out, err = run_main(compare, capsys, "--test", str(path))

    assert code == 0
    assert "· externe ·" in out
    assert SECRET not in out + err
    assert "Zorglub" not in out + err


def test_a_file_named_like_the_internal_test_is_hidden_even_without_the_flag(
    compare: ModuleType, tmp_path: Path
) -> None:
    path = write(tmp_path, CASES, "verifier_test.yaml")

    dataset = compare.resolve_dataset(compare._parser().parse_args([str(path)]))

    assert dataset.hidden is True


@pytest.mark.parametrize("extra", [[], ["--test"]])
def test_an_unusable_file_is_refused_without_quoting_a_phrase(
    compare: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path, extra: list[str]
) -> None:
    broken = CASES.replace("expected: [calendar.write]", "expected: [inconnu.effet]", 1)
    path = write(tmp_path, broken, "verifier_test.yaml")

    code, out, err = run_main(compare, capsys, *extra, str(path))

    assert code == 1 and out == ""
    assert "Fichier de cas inutilisable" in err
    assert "ajouté" not in err and SECRET not in err


@pytest.mark.parametrize("content", ["", "domains: {}\ncases: []\n", "::: pas du yaml ["])
def test_empty_or_invalid_files_are_refused(
    compare: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path, content: str
) -> None:
    code, _, err = run_main(compare, capsys, str(write(tmp_path, content)))

    assert code == 1
    assert "Fichier de cas inutilisable" in err


def test_a_missing_file_is_refused(
    compare: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, _, err = run_main(compare, capsys, str(tmp_path / "absent.yaml"))

    assert code == 1
    assert "absent.yaml" in err


def test_unknown_detector_is_refused_with_the_valid_names(
    compare: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, out, err = run_main(
        compare, capsys, "--detectors", "rules,magie", str(write(tmp_path, CASES))
    )

    assert code == 1 and out == ""
    assert "magie" in err and "rules" in err


@pytest.mark.parametrize("value", ["", " , "])
def test_no_detector_is_refused(
    compare: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path, value: str
) -> None:
    code, _, err = run_main(compare, capsys, "--detectors", value, str(write(tmp_path, CASES)))

    assert code == 1
    assert "Aucun détecteur" in err


def test_a_detector_that_fails_is_reported_not_hidden_by_the_fallback(
    compare: ModuleType,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(factory._BUILDERS, "cassé", Broken)
    path = write(tmp_path, CASES, "aveugle.yaml")

    code, out, err = run_main(compare, capsys, "--test", "--detectors", "cassé", str(path))

    assert code == 1 and out == ""
    assert "cassé" in err and "RuntimeError" in err
    # Sur un jeu caché, le message de l'exception (qui cite la phrase) est masqué.
    assert SECRET not in err


def test_duplicate_detector_names_are_measured_once(
    compare: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    _, out, _ = run_main(
        compare, capsys, "--detectors", "rules,rules", "--no-latency", str(write(tmp_path, CASES))
    )

    assert out.count("== rules") == 1


# --- MLflow ------------------------------------------------------------------------------------


def measurement_of(compare: ModuleType, tmp_path: Path, content: str, hidden: bool) -> Any:
    dataset = compare.Dataset("externe", (write(tmp_path, content),), hidden)
    return compare.measure(
        RulesDetector(),
        dataset,
        compare.load_dataset(dataset),
        warmup_rounds=1,
        repeats=3,
    )


def test_payload_has_params_tags_metrics_and_a_report_without_phrases(
    compare: ModuleType, tmp_path: Path
) -> None:
    measurement = measurement_of(compare, tmp_path, CASES, hidden=True)

    payload = compare.build_payload(measurement, compare.GitInfo("abc123", True))

    assert payload.name == "rules-externe"
    assert payload.params["detector"] == "rules"
    assert payload.params["phrases"] == 5
    assert payload.params["latency_measures"] == 15
    assert payload.params["latency_repeats"] == 3
    assert payload.params["latency_warmup_rounds"] == 1
    assert payload.params["machine"] == payload.tags["machine"]
    assert "cœurs logiques" in str(payload.tags["machine"])
    assert payload.tags["git_commit"] == "abc123"
    assert payload.tags["git_dirty"] == "true"
    assert payload.tags["dataset_sha256"] == measurement.dataset.sha256
    assert payload.tags["dataset_hidden"] == "true"
    for metric in ("detection", "false_alarms", "latency_p50_ms", "latency_p95_ms"):
        assert metric in payload.metrics
    assert payload.metrics["detection_wilson_low"] <= payload.metrics["detection"]
    assert payload.metrics["detection"] <= payload.metrics["detection_wilson_high"]
    assert "effect.calendar.write.detection" in payload.metrics
    assert "effect.email.send.false_alarms_wilson_high" in payload.metrics
    everything = repr((payload.params, payload.tags, payload.metrics, payload.report))
    assert SECRET not in everything and "Zorglub" not in everything


def test_payload_leaves_out_a_rate_without_cases_instead_of_inventing_one(
    compare: ModuleType, tmp_path: Path
) -> None:
    measurement = measurement_of(compare, tmp_path, ONLY_POSITIVES, hidden=False)

    payload = compare.build_payload(measurement, compare.GitInfo("inconnu", None))

    assert "detection" in payload.metrics
    assert "false_alarms" not in payload.metrics
    assert payload.tags["git_dirty"] == "inconnu"


def test_payload_without_latency_has_no_latency_metric(compare: ModuleType, tmp_path: Path) -> None:
    dataset = compare.Dataset("fichiers", (write(tmp_path, CASES),), False)
    measurement = compare.measure(
        RulesDetector(), dataset, compare.load_dataset(dataset), latency=False
    )

    payload = compare.build_payload(measurement, compare.GitInfo("x", False))

    assert "latency_p95_ms" not in payload.metrics
    assert "latency_repeats" not in payload.params


def test_git_info_reads_the_commit_of_a_repository(compare: ModuleType) -> None:
    info = compare.git_info()

    assert re.fullmatch(r"[0-9a-f]{40}", info.commit)
    assert isinstance(info.dirty, bool)


def test_git_info_without_a_repository_or_git_says_unknown(
    compare: ModuleType, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_git(*args: Any, **kwargs: Any) -> None:
        raise FileNotFoundError("git")

    monkeypatch.setattr(subprocess, "run", no_git)

    assert compare.git_info(tmp_path) == compare.GitInfo("inconnu", None)


def test_mlflow_missing_is_reported_with_the_command_to_run(
    compare: ModuleType, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setitem(sys.modules, "mlflow", None)

    with pytest.raises(compare.CompareError, match="uv sync --group eval"):
        compare.log_to_mlflow([], "sqlite:///inutile.db")


def test_mlflow_runs_are_written_and_readable(compare: ModuleType, tmp_path: Path) -> None:
    mlflow = pytest.importorskip("mlflow")
    uri = f"sqlite:///{(tmp_path / 'mlflow.db').as_posix()}"
    measurement = measurement_of(compare, tmp_path, CASES, hidden=True)
    payload = compare.build_payload(measurement, compare.GitInfo("abc123", False))

    compare.log_to_mlflow([payload, payload], uri, "essai")

    client = mlflow.MlflowClient(uri)
    experiment = client.get_experiment_by_name("essai")
    runs = client.search_runs([experiment.experiment_id])
    assert len(runs) == 2
    run = runs[0]
    assert run.info.run_name == "rules-externe"
    assert run.data.params["detector"] == "rules"
    assert run.data.tags["dataset_sha256"] == measurement.dataset.sha256
    assert run.data.tags["git_commit"] == "abc123"
    assert run.data.metrics["detection"] == payload.metrics["detection"]
    assert [artifact.path for artifact in client.list_artifacts(run.info.run_id)] == ["rapport.txt"]


def test_main_with_mlflow_writes_one_run_per_detector(
    compare: ModuleType,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    sent: list[Any] = []
    monkeypatch.setitem(factory._BUILDERS, "muet", Silent)
    monkeypatch.setattr(
        compare, "log_to_mlflow", lambda payloads, uri, experiment: sent.extend(payloads)
    )
    path = write(tmp_path, CASES)

    code, out, _ = run_main(
        compare, capsys, "--detectors", "rules,muet", "--mlflow", "--no-latency", str(path)
    )

    assert code == 0
    assert [payload.name for payload in sent] == ["rules-fichiers", "muet-fichiers"]
    assert "2 run(s) MLflow" in out


def test_main_reports_a_missing_mlflow_and_still_prints_the_measures(
    compare: ModuleType,
    capsys: pytest.CaptureFixture[str],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(sys.modules, "mlflow", None)

    code, out, err = run_main(compare, capsys, "--mlflow", str(write(tmp_path, CASES)))

    assert code == 1
    assert "== rules" in out
    assert "uv sync --group eval" in err
