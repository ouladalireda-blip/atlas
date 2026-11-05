"""Tests du runner d'évaluation : chiffres par effet, jeu de test sans détail, planchers.

Les jeux sont synthétiques. Seuls les tests de la fin lancent le vrai runner sur les vrais jeux,
et ils ne vérifient que des codes de retour et des chiffres, sans jamais citer une phrase.
"""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest
import yaml

EVALS = Path(__file__).parent.parent / "evals"
SECRET = "Phrase-secrète-du-jeu-de-test"

VOCABULARY = """
domains:
  calendar:
    nouns: [événement]
    actions: {write: calendar.write}
  email:
    nouns: [mail]
    actions: {send: email.send}
"""
CASES = f"""{VOCABULARY}
cases:
  - reply: "L'événement est ajouté."
    expected: [calendar.write]
  - reply: "Le mail est envoyé et l'événement est créé."
    expected: [calendar.write, email.send]
  - reply: "J'ai ajouté le mail de Sam à l'événement, {SECRET}."
    expected: [calendar.write]
  - reply: "Tu as un événement demain."
    expected: []
  - reply: "Le mail envoyé par la banque est dans ta boîte."
    expected: []
"""


@pytest.fixture(scope="module")
def runner() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "runner_under_test", EVALS / "run_verifier_eval.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def config_text(dev: float = 0.0, alarms: float = 1.0, test_detection: float = 0.0) -> str:
    return yaml.safe_dump(
        {
            "sets": {"dev": ["dev.yaml"], "test": ["verifier_test.yaml"]},
            "floors": {
                "dev": {"detection": dev, "false_alarms": alarms},
                "test": {"detection": test_detection, "false_alarms": alarms},
            },
        }
    )


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    (tmp_path / "dev.yaml").write_text(CASES, encoding="utf-8")
    (tmp_path / "verifier_test.yaml").write_text(CASES, encoding="utf-8")
    (tmp_path / "thresholds.yaml").write_text(config_text(), encoding="utf-8")
    return tmp_path


def run(runner: ModuleType, workspace: Path, *arguments: str) -> int:
    return int(runner.main([*arguments, "--config", str(workspace / "thresholds.yaml")]))


# --- chiffres par effet -----------------------------------------------------------------------


def test_per_effect_counts_follow_the_definition(runner: ModuleType, workspace: Path) -> None:
    report = runner.evaluate(runner.load_eval(workspace / "dev.yaml"))

    calendar, email = report.effects["calendar.write"], report.effects["email.send"]

    # calendar.write est attendu dans 3 phrases ; dans la 3e, « événement » est à plus de 6 mots du
    # participe, donc il n'est pas trouvé. Jamais de fausse alerte.
    assert (calendar.positives, calendar.detected) == (3, 2)
    assert (calendar.negatives, calendar.false_alarms) == (2, 0)
    # email.send est attendu dans 1 phrase (trouvé) ; il est trouvé à tort dans 1 des 4 autres
    # (le « mail envoyé par la banque »). La phrase 3 vise le mail, le nom le plus proche du
    # verbe, avec l'action « write » que le domaine email ne déclare pas : pas d'email.send.
    assert (email.positives, email.detected) == (1, 1)
    assert (email.negatives, email.false_alarms) == (4, 1)
    # Globalement, une phrase multi-effets compte une fois et demande les deux effets exacts.
    assert (report.positives, report.detected) == (3, 2)
    assert (report.negatives, report.false_alarms) == (2, 1)
    assert len(report.errors) == 2


def test_effects_are_added_up_across_files(runner: ModuleType, workspace: Path) -> None:
    data = runner.load_eval(workspace / "dev.yaml")
    single = runner.evaluate(data)

    both = runner.combine([single, single])

    assert both.positives == 2 * single.positives
    assert (
        both.effects["calendar.write"].positives == 2 * single.effects["calendar.write"].positives
    )
    assert len(both.errors) == 2 * len(single.errors)


def test_aggregate_report_lists_each_effect_and_no_sentence(
    runner: ModuleType, workspace: Path
) -> None:
    report = runner.evaluate(runner.load_eval(workspace / "dev.yaml"))

    text = runner.format_aggregate(report, "dev")

    assert "calendar.write" in text and "email.send" in text
    assert "détection" in text and "fausses alertes" in text
    assert SECRET not in text
    assert "ajouté" not in text and "Erreurs" not in text


def test_rates_are_not_invented_without_cases(runner: ModuleType, tmp_path: Path) -> None:
    only_negative = f"{VOCABULARY}\ncases:\n  - reply: Bonjour.\n    expected: []\n"
    (tmp_path / "n.yaml").write_text(only_negative, encoding="utf-8")

    text = runner.format_aggregate(runner.evaluate(runner.load_eval(tmp_path / "n.yaml")), "dev")

    assert "non mesurable (0/0)" in text


# --- le jeu de test ne montre rien ------------------------------------------------------------


def output_of(capsys: pytest.CaptureFixture[str]) -> str:
    captured = capsys.readouterr()
    return captured.out + captured.err


def test_test_mode_prints_only_figures(
    runner: ModuleType, workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(runner, workspace, "--test") == 0

    output = output_of(capsys)
    assert "== test :" in output
    assert "Par effet" in output and "calendar.write" in output
    assert SECRET not in output
    assert "Erreurs" not in output and "attendu" not in output and "détecté :" not in output


def test_dev_mode_shows_the_errors(
    runner: ModuleType, workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(runner, workspace) == 0

    output = output_of(capsys)
    assert "== dev :" in output
    assert "Erreurs" in output
    assert SECRET in output  # le développement, lui, se lit


def test_the_test_file_is_never_detailed_even_when_given_by_path(
    runner: ModuleType, workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    run(runner, workspace, str(workspace / "verifier_test.yaml"))

    output = output_of(capsys)
    assert SECRET not in output
    assert "Erreurs" not in output


def test_a_file_named_like_the_test_file_is_hidden_without_any_config(
    runner: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "verifier_test.yaml").write_text(CASES, encoding="utf-8")

    code = runner.main([str(tmp_path / "verifier_test.yaml"), "--config", str(tmp_path / "x.yaml")])

    assert code == 0
    assert SECRET not in output_of(capsys)


def test_an_invalid_test_file_does_not_quote_its_sentences(
    runner: ModuleType, workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (workspace / "verifier_test.yaml").write_text(
        VOCABULARY + f'\ncases:\n  - reply: "{SECRET}"\n    expected: [inconnu.effet]\n',
        encoding="utf-8",
    )

    assert run(runner, workspace, "--test") == 1
    assert run(runner, workspace, "--check", "--test") == 1
    assert run(runner, workspace, str(workspace / "verifier_test.yaml")) == 1

    output = output_of(capsys)
    assert SECRET not in output
    assert "détail masqué" in output


def test_the_floor_check_on_the_test_set_never_prints_errors(
    runner: ModuleType, workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (workspace / "thresholds.yaml").write_text(config_text(test_detection=1.0), encoding="utf-8")

    code = run(runner, workspace, "--check", "--test")

    output = output_of(capsys)
    assert code == 1
    assert "PLANCHER FRANCHI" in output
    assert SECRET not in output
    assert "Erreurs" not in output


# --- planchers --------------------------------------------------------------------------------


def test_floors_pass_when_met_and_fail_when_crossed(
    runner: ModuleType, workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    assert run(runner, workspace, "--check") == 0
    assert "Planchers dev : respectés" in output_of(capsys)

    (workspace / "thresholds.yaml").write_text(config_text(dev=0.99), encoding="utf-8")
    assert run(runner, workspace, "--check") == 1
    assert "détection" in output_of(capsys)

    (workspace / "thresholds.yaml").write_text(config_text(alarms=0.0), encoding="utf-8")
    assert run(runner, workspace, "--check") == 1
    assert "plafond" in output_of(capsys)


def test_dev_and_test_have_separate_floors(runner: ModuleType, workspace: Path) -> None:
    (workspace / "thresholds.yaml").write_text(
        config_text(dev=0.0, test_detection=1.0), encoding="utf-8"
    )

    assert run(runner, workspace, "--check") == 0
    assert run(runner, workspace, "--check", "--test") == 1


def test_per_effect_floor_is_enforced(runner: ModuleType, workspace: Path) -> None:
    config = yaml.safe_load(config_text())
    config["floors"]["dev"]["effects"] = {"email.send": {"detection": 0.0, "false_alarms": 0.1}}
    path = workspace / "thresholds.yaml"
    path.write_text(yaml.safe_dump(config), encoding="utf-8")
    loaded = runner.load_config(path)

    problems = runner.check_floors(
        runner.evaluate_set(loaded, "dev", workspace), loaded.floors["dev"]
    )

    # email.send : 2 fausses alertes sur 4 phrases, soit 50 %, au-dessus du plafond de 10 %.
    assert len(problems) == 1
    assert problems[0].startswith("email.send") and "plafond" in problems[0]


def test_a_floor_on_an_unknown_effect_fails(runner: ModuleType, workspace: Path) -> None:
    config = yaml.safe_load(config_text())
    config["floors"]["dev"]["effects"] = {"ghost.write": {"detection": 0.5, "false_alarms": 1.0}}
    (workspace / "thresholds.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    assert run(runner, workspace, "--check") == 1


def test_missing_floor_for_a_set_fails_loudly(
    runner: ModuleType, workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    config = yaml.safe_load(config_text())
    del config["floors"]["test"]
    (workspace / "thresholds.yaml").write_text(yaml.safe_dump(config), encoding="utf-8")

    assert run(runner, workspace, "--check", "--test") == 1
    assert "Aucun plancher" in output_of(capsys)


def test_write_floors_records_the_current_level_without_hardening(
    runner: ModuleType, workspace: Path
) -> None:
    assert run(runner, workspace, "--write-floors") == 0

    written = runner.load_config(workspace / "thresholds.yaml")
    report = runner.evaluate_set(written, "dev", workspace)
    assert 0 < written.floors["dev"].detection <= report.detected / report.positives
    assert written.floors["dev"].false_alarms >= report.false_alarms / report.negatives
    assert set(written.floors["dev"].effects) == {"calendar.write", "email.send"}
    # Ce qui vient d'être écrit passe.
    assert run(runner, workspace, "--check") == 0
    assert run(runner, workspace, "--check", "--test") == 0


def test_rounding_never_makes_a_floor_stricter_than_the_measure(runner: ModuleType) -> None:
    stats = runner.EffectStats(positives=3, detected=1, negatives=3, false_alarms=1)
    report = runner.EvalReport(3, 1, 3, 1, [], {"a.b": stats})

    floors = runner.current_floors(report)

    assert floors.detection <= 1 / 3 and floors.false_alarms >= 1 / 3
    assert 1 / 3 - floors.detection < 1e-3


# --- configuration invalide -------------------------------------------------------------------


@pytest.mark.parametrize(
    "content",
    [
        "pas: une config",
        "sets: {dev: [a.yaml]}",
        "sets: {dev: [a.yaml], test: []}",
        "sets: {dev: [a.yaml], test: [b.yaml], autre: [c.yaml]}",
        "sets: {dev: [a.yaml], test: [b.yaml]}\nfloors: {prod: {detection: 1, false_alarms: 0}}",
        "sets: {dev: [a.yaml], test: [b.yaml]}\nfloors: {dev: {detection: 2, false_alarms: 0}}",
        "[: invalide",
    ],
)
def test_invalid_configuration_is_refused(
    runner: ModuleType, workspace: Path, content: str, capsys: pytest.CaptureFixture[str]
) -> None:
    (workspace / "thresholds.yaml").write_text(content, encoding="utf-8")

    assert run(runner, workspace) == 1
    assert "Configuration inutilisable" in output_of(capsys)


def test_missing_configuration_is_refused(runner: ModuleType, tmp_path: Path) -> None:
    assert runner.main(["--config", str(tmp_path / "absent.yaml")]) == 1


def test_missing_case_file_is_refused(runner: ModuleType, workspace: Path) -> None:
    (workspace / "dev.yaml").unlink()

    assert run(runner, workspace) == 1


def test_explicit_files_do_not_mix_with_modes(runner: ModuleType, workspace: Path) -> None:
    assert run(runner, workspace, str(workspace / "dev.yaml"), "--check") == 1


# --- les vrais jeux : codes de retour et chiffres seulement -----------------------------------


def test_real_development_floors_are_respected(
    runner: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    # Le jeu de test réel n'est jamais mesuré par pytest (donc pas à chaque push) : voir ci-dessous.
    assert runner.main(["--check"]) == 0

    capsys.readouterr()


def test_real_configuration_lists_the_expected_files_and_effects(runner: ModuleType) -> None:
    config = runner.load_config(runner.DEFAULT_CONFIG)

    assert config.sets["dev"] == ["verifier_cases.yaml", "verifier_dev.yaml"]
    assert config.sets["test"] == ["verifier_test.yaml"]
    expected = {"calendar.write", "email.send", "tasks.write", "tasks.delete"}
    assert set(config.floors["dev"].effects) == expected
    assert set(config.floors["test"].effects) == expected


# --- effectifs et mise en garde -----------------------------------------------------------------


@pytest.mark.parametrize(
    ("total", "flagged"), [(0, True), (1, True), (9, True), (10, False), (11, False), (500, False)]
)
def test_small_samples_are_flagged_from_ten_cases(
    runner: ModuleType, total: int, flagged: bool
) -> None:
    text = runner._rate(total // 2, total)

    assert (f"({total // 2}/{total})" in text) is True  # les effectifs sont toujours là
    assert ("[effectif insuffisant]" in text) is flagged


def test_every_category_shows_its_counts_and_is_flagged_on_its_own_denominator(
    runner: ModuleType, tmp_path: Path
) -> None:
    cases = [
        {"reply": f"L'événement {n} est ajouté.", "expected": ["calendar.write"]} for n in range(12)
    ] + [{"reply": f"Bonjour numéro {n}.", "expected": []} for n in range(3)]
    document = {
        "domains": {
            "calendar": {"nouns": ["événement"], "actions": {"write": "calendar.write"}},
            "email": {"nouns": ["mail"], "actions": {"send": "email.send"}},
        }
    }
    (tmp_path / "c.yaml").write_text(
        yaml.safe_dump({**document, "cases": cases}, allow_unicode=True), encoding="utf-8"
    )
    report = runner.evaluate(runner.load_eval(tmp_path / "c.yaml"))

    lines = {
        line.split()[0]: line for line in runner.format_aggregate(report, "dev").splitlines()[4:]
    }

    # calendar.write : 12 positives (assez), 3 négatives (insuffisant) ; email.send : l'inverse.
    assert "détection 100.0% (12/12) ;" in lines["calendar.write"]
    assert "fausses alertes 0.0% (0/3) [effectif insuffisant]" in lines["calendar.write"]
    assert "détection non mesurable (0/0) [effectif insuffisant]" in lines["email.send"]
    assert "fausses alertes 0.0% (0/15)" in lines["email.send"]
    assert "(0/15) [effectif" not in lines["email.send"]


def test_the_global_lines_carry_the_warning_too(runner: ModuleType, workspace: Path) -> None:
    report = runner.evaluate(runner.load_eval(workspace / "dev.yaml"))  # 3 positives, 2 négatives

    for text in (runner.format_aggregate(report, "dev"), runner.format_report(report)):
        assert "(2/3) [effectif insuffisant]" in text
        assert "(1/2) [effectif insuffisant]" in text


# --- jeu de test hors du dépôt ------------------------------------------------------------------


def test_an_outside_file_is_measured_as_a_test_set_with_figures_only(
    runner: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    outside = (
        tmp_path / "ailleurs" / "aveugle-2026.yaml"
    )  # ni dans le dépôt, ni nommé comme le test
    outside.parent.mkdir()
    outside.write_text(CASES, encoding="utf-8")

    code = runner.main(["--test", str(outside), "--config", str(tmp_path / "absent.yaml")])

    output = output_of(capsys)
    assert code == 0
    assert "== test externe : 5 phrases ==" in output
    assert "calendar.write" in output and "(2/3)" in output
    assert SECRET not in output
    assert "Erreurs" not in output and "attendu" not in output


def test_several_outside_files_are_added_up(
    runner: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    for name in ("a.yaml", "b.yaml"):
        (tmp_path / name).write_text(CASES, encoding="utf-8")

    runner.main(["--test", str(tmp_path / "a.yaml"), str(tmp_path / "b.yaml")])

    assert "== test externe : 10 phrases ==" in output_of(capsys)


def test_an_outside_file_without_test_flag_is_still_detailed_for_development(
    runner: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    (tmp_path / "libre.yaml").write_text(CASES, encoding="utf-8")

    runner.main([str(tmp_path / "libre.yaml")])

    assert SECRET in output_of(capsys)


def test_an_unreadable_outside_file_never_quotes_a_sentence(
    runner: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    bad = tmp_path / "aveugle.yaml"
    bad.write_text(
        VOCABULARY + f'\ncases:\n  - reply: "{SECRET}"\n    expected: [inconnu.effet]\n',
        encoding="utf-8",
    )

    assert runner.main(["--test", str(bad)]) == 1
    assert runner.main(["--test", str(tmp_path / "absent.yaml")]) == 1

    output = output_of(capsys)
    assert SECRET not in output
    assert "détail masqué" in output


def test_outside_files_do_not_combine_with_floors(runner: ModuleType, tmp_path: Path) -> None:
    (tmp_path / "x.yaml").write_text(CASES, encoding="utf-8")

    assert runner.main(["--test", "--check", str(tmp_path / "x.yaml")]) == 1


# --- politique de la CI -------------------------------------------------------------------------


def ci_steps() -> list[dict[str, object]]:
    workflow = yaml.safe_load(
        (EVALS.parent / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    )
    steps = workflow["jobs"]["checks"]["steps"]
    assert isinstance(steps, list)
    return steps


def test_ci_checks_development_on_every_push_and_the_test_set_only_on_tags() -> None:
    commands = {str(step["run"]): step for step in ci_steps() if "run" in step}

    development = commands["uv run python evals/run_verifier_eval.py --check"]
    assert "if" not in development
    test = commands["uv run python evals/run_verifier_eval.py --check --test"]
    assert test["if"] == "startsWith(github.ref, 'refs/tags/v')"
    assert sum("--test" in command for command in commands) == 1


def test_ci_is_triggered_by_tags() -> None:
    workflow = yaml.safe_load(
        (EVALS.parent / ".github" / "workflows" / "ci.yml").read_text("utf-8")
    )
    triggers = workflow.get("on", workflow.get(True))

    assert "push" in triggers  # sans filtre : branches et tags


# --- affirmations génériques de classe -----------------------------------------------------------

SHARED = """
domains:
  calendar:
    nouns: [événement]
    actions: {write: calendar.write}
  tasks:
    nouns: [tâche]
    actions: {write: tasks.write, delete: tasks.delete}
cases:
  - reply: "C'est ajouté !"
    expected: [calendar.write]
  - reply: "C'est ajouté !"
    expected: [tasks.write]
  - reply: "C'est ajouté !"
    expected: []
  - reply: "C'est ajouté !"
    expected: [tasks.delete]
  - reply: "Supprimé !"
    expected: [tasks.delete]
"""


def test_a_generic_class_claim_counts_for_the_annotated_effect_it_can_prove(
    runner: ModuleType, tmp_path: Path
) -> None:
    (tmp_path / "s.yaml").write_text(SHARED, encoding="utf-8")

    report = runner.evaluate(runner.load_eval(tmp_path / "s.yaml"))

    # Cas 1 et 2 : « *.write » est couvert par calendar.write ou tasks.write -> détection.
    # Cas 4 : l'annotation attend tasks.delete, que « *.write » ne peut pas prouver -> manqué.
    # Cas 5 : « Supprimé ! » n'a qu'un domaine pour delete -> tasks.delete exact -> détection.
    assert (report.positives, report.detected) == (4, 3)
    # Cas 3 : rien n'était attendu et une affirmation générique est trouvée -> fausse alerte.
    assert (report.negatives, report.false_alarms) == (1, 1)
    errors = {error.reply: error.detected for error in report.errors}
    assert ["*.write"] in errors.values()
    assert report.effects["calendar.write"].detected == 1
    assert report.effects["tasks.write"].detected == 1
    assert report.effects["tasks.delete"].detected == 1
    assert report.effects["calendar.write"].false_alarms == 0
