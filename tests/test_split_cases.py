"""Tests du découpage développement / test (jeux synthétiques : aucune lecture du vrai test)."""

import importlib.util
import random
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest
import yaml

EVALS = Path(__file__).parent.parent / "evals"


@pytest.fixture(scope="module")
def splitter() -> ModuleType:
    spec = importlib.util.spec_from_file_location("split_cases", EVALS / "split_cases.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def make_cases() -> list[dict[str, Any]]:
    cases: list[dict[str, Any]] = []
    plan = [
        ([], 11),
        (["calendar.write"], 8),
        (["email.send"], 5),
        (["tasks.write"], 7),
        (["calendar.write", "email.send"], 3),
        (["email.send", "tasks.write"], 1),
    ]
    for effects, count in plan:
        for number in range(count):
            cases.append(
                {"reply": f"phrase {'+'.join(effects) or 'aucun'} {number}", "expected": effects}
            )
    return cases


def write_source(path: Path, cases: list[dict[str, Any]]) -> Path:
    domains = {
        "calendar": {"nouns": ["agenda"], "actions": {"write": "calendar.write"}},
        "email": {"nouns": ["mail"], "actions": {"send": "email.send"}},
        "tasks": {"nouns": ["tâche"], "actions": {"write": "tasks.write"}},
    }
    path.write_text(yaml.safe_dump({"domains": domains, "cases": cases}), encoding="utf-8")
    return path


def test_every_sentence_lands_in_exactly_one_set(splitter: ModuleType) -> None:
    cases = make_cases()

    dev, test = splitter.split(cases)

    assert sorted(dev + test) == list(range(len(cases)))
    assert set(dev).isdisjoint(test)


def test_each_stratum_is_halved(splitter: ModuleType) -> None:
    cases = make_cases()
    dev, test = splitter.split(cases)

    for key in {splitter.stratum(case) for case in cases}:
        in_dev = sum(1 for p in dev if splitter.stratum(cases[p]) == key)
        in_test = sum(1 for p in test if splitter.stratum(cases[p]) == key)
        assert abs(in_dev - in_test) <= 1, key


def test_odd_surplus_alternates_so_sizes_stay_close(splitter: ModuleType) -> None:
    dev, test = splitter.split(make_cases())

    assert abs(len(dev) - len(test)) <= 2


def test_multi_effect_sentences_are_spread_too(splitter: ModuleType) -> None:
    cases = make_cases()
    dev, test = splitter.split(cases)

    multi = ("calendar.write", "email.send")
    assert {splitter.stratum(cases[p]) for p in dev} >= {multi}
    assert {splitter.stratum(cases[p]) for p in test} >= {multi}


def test_split_is_deterministic_and_independent_of_input_order(splitter: ModuleType) -> None:
    cases = make_cases()
    shuffled = cases[:]
    random.Random(1).shuffle(shuffled)

    first = splitter.split(cases)
    again = splitter.split(cases)
    reordered_dev, reordered_test = splitter.split(shuffled)

    assert first == again
    assert {cases[p]["reply"] for p in first[0]} == {shuffled[p]["reply"] for p in reordered_dev}
    assert {cases[p]["reply"] for p in first[1]} == {shuffled[p]["reply"] for p in reordered_test}


def test_seed_changes_the_split(splitter: ModuleType) -> None:
    cases = make_cases()

    assert splitter.split(cases, seed=1) != splitter.split(cases, seed=2)


def run(splitter: ModuleType, tmp_path: Path, *extra: str) -> int:
    return int(
        splitter.main(
            [
                "--source",
                str(tmp_path / "source.yaml"),
                "--dev",
                str(tmp_path / "dev.yaml"),
                "--test",
                str(tmp_path / "test.yaml"),
                *extra,
            ]
        )
    )


def test_main_writes_both_files_and_prints_no_sentence(
    splitter: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    cases = make_cases()
    write_source(tmp_path / "source.yaml", cases)

    assert run(splitter, tmp_path) == 0

    output = capsys.readouterr()
    assert not any(case["reply"] in output.out + output.err for case in cases)
    dev = yaml.safe_load((tmp_path / "dev.yaml").read_text(encoding="utf-8"))
    test = yaml.safe_load((tmp_path / "test.yaml").read_text(encoding="utf-8"))
    assert len(dev["cases"]) + len(test["cases"]) == len(cases)
    assert dev["domains"] == test["domains"]
    replies = [c["reply"] for c in dev["cases"] + test["cases"]]
    assert sorted(replies) == sorted(c["reply"] for c in cases)
    assert "NE JAMAIS LIRE" in (tmp_path / "test.yaml").read_text(encoding="utf-8")


def test_rerun_is_idempotent_but_never_overwrites_a_different_file(
    splitter: ModuleType, tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    write_source(tmp_path / "source.yaml", make_cases())
    run(splitter, tmp_path)
    before = (tmp_path / "test.yaml").read_text(encoding="utf-8")

    assert run(splitter, tmp_path) == 0
    assert "déjà à jour" in capsys.readouterr().out

    changed = make_cases() + [{"reply": "nouvelle phrase", "expected": []}]
    write_source(tmp_path / "source.yaml", changed)
    assert run(splitter, tmp_path) == 1
    assert "refus de l'écraser" in capsys.readouterr().err
    assert (tmp_path / "test.yaml").read_text(encoding="utf-8") == before


def test_force_overwrites(splitter: ModuleType, tmp_path: Path) -> None:
    write_source(tmp_path / "source.yaml", make_cases())
    run(splitter, tmp_path)
    write_source(tmp_path / "source.yaml", make_cases()[:-1])

    assert run(splitter, tmp_path, "--force") == 0


@pytest.mark.parametrize(
    "content",
    [
        "pas: un jeu",
        "domains: {a: {nouns: [x], actions: {write: a.b}}}\ncases: []",
        "domains: {a: {nouns: [x], actions: {write: a.b}}}\ncases: [{reply: a}]",
        "domains: {a: {nouns: [x], actions: {write: a.b}}}\n"
        "cases: [{reply: a, expected: []}, {reply: a, expected: []}]",
        "[: invalide",
    ],
)
def test_invalid_source_is_refused_and_nothing_is_written(
    splitter: ModuleType, tmp_path: Path, content: str
) -> None:
    (tmp_path / "source.yaml").write_text(content, encoding="utf-8")

    assert run(splitter, tmp_path) == 1
    assert not (tmp_path / "dev.yaml").exists()
    assert not (tmp_path / "test.yaml").exists()


def test_missing_source_is_refused(splitter: ModuleType, tmp_path: Path) -> None:
    assert run(splitter, tmp_path) == 1


# --- fichiers réels : cohérence, par effectifs seulement (aucune phrase n'est affichée) -----------


def read_replies(name: str) -> list[str]:
    data = yaml.safe_load((EVALS / name).read_text(encoding="utf-8"))
    return [case["reply"] for case in data["cases"]]


def test_real_dev_and_test_files_partition_the_independent_set() -> None:
    source = set(read_replies("independent_cases.yaml"))
    dev = set(read_replies("verifier_dev.yaml"))
    test = set(read_replies("verifier_test.yaml"))
    development = set(read_replies("verifier_cases.yaml"))

    # Des entiers seulement : un échec ne doit citer aucune phrase du jeu de test.
    assert len(dev & test) == 0
    assert len((dev | test) ^ source) == 0
    assert abs(len(dev) - len(test)) <= 2
    assert len(development & test) == 0
