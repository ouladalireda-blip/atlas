"""Tests du contrôle de fuite : détection des doublons, et aucune phrase dans la sortie."""

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

EVALS = Path(__file__).parent.parent / "evals"
SECRET = "Zorglub a planifié le rendez-vous secret de Marcel."
HEADER = """
domains:
  calendar:
    nouns: [rendez-vous]
    actions: {write: calendar.write}
cases:
"""


@pytest.fixture(scope="module")
def leak() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "check_leakage_under_test", EVALS / "check_leakage.py"
    )
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def cases_file(tmp_path: Path, name: str, replies: list[str]) -> str:
    body = "".join(f'  - reply: "{reply}"\n    expected: []\n' for reply in replies)
    (tmp_path / name).write_text(HEADER + body, encoding="utf-8")
    return name


def make_config(tmp_path: Path, dev: list[str], test: list[str]) -> Path:
    dev_name = cases_file(tmp_path, "dev.yaml", dev)
    test_name = cases_file(tmp_path, "secret.yaml", test)
    config = tmp_path / "thresholds.yaml"
    config.write_text(
        f"sets:\n  dev: [{dev_name}]\n  test: [{test_name}]\nfloors: {{}}\n", encoding="utf-8"
    )
    return config


def run_main(
    leak: ModuleType, capsys: pytest.CaptureFixture[str], *arguments: str
) -> tuple[int, str, str]:
    code = leak.main(list(arguments))
    captured = capsys.readouterr()
    return code, captured.out, captured.err


def test_normalize_ignores_case_accents_punctuation_and_spaces(leak: ModuleType) -> None:
    assert leak.normalize("  L’Événement  est AJOUTÉ !! ") == "l evenement est ajoute"
    assert leak.normalize("L'événement est ajouté.") == leak.normalize("l’evenement EST ajoute")
    assert leak.normalize("") == ""


def test_words_are_the_set_of_normalized_words(leak: ModuleType) -> None:
    assert leak.words("a b a c") == frozenset({"a", "b", "c"})
    assert leak.words("") == frozenset()


def test_jaccard(leak: ModuleType) -> None:
    left = leak.words("a b c d")
    assert leak.jaccard(left, left) == 1.0
    assert leak.jaccard(left, leak.words("x y z")) == 0.0
    assert leak.jaccard(frozenset(), frozenset()) == 0.0
    assert leak.jaccard(left, leak.words("a b c e")) == pytest.approx(3 / 5)


def test_exact_duplicates_survive_case_accents_and_punctuation(leak: ModuleType) -> None:
    result = leak.overlap(["Le mail est parti !", "Autre chose"], ["le MAIL est parti"])

    assert result.exact == 1
    assert result.near == 0


def test_near_duplicates_need_the_threshold_and_are_not_counted_as_exact(leak: ModuleType) -> None:
    first = ["Le mail pour Sam est bien parti ce matin"]
    second = ["Le mail pour Sam est bien parti hier matin"]

    close = leak.overlap(first, second, 0.5)
    strict = leak.overlap(first, second, 0.95)

    assert (close.exact, close.near) == (0, 1)
    assert (strict.exact, strict.near) == (0, 0)


def test_unrelated_sets_do_not_overlap(leak: ModuleType) -> None:
    result = leak.overlap(["Le mail est parti"], ["Tu as un rendez-vous demain à neuf heures"])

    assert (result.exact, result.near) == (0, 0)


def test_a_phrase_is_counted_once_even_with_several_matches(leak: ModuleType) -> None:
    result = leak.overlap(["Le mail est parti"], ["le mail est parti", "Le mail est parti."])

    assert result.exact == 1


def test_internal_repetitions(leak: ModuleType) -> None:
    assert leak.internal_duplicates(["A b", "a  B!", "c"]) == 1
    assert leak.internal_duplicates([]) == 0


def test_clean_sets_pass_and_the_report_has_only_counts(
    leak: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config = make_config(tmp_path, ["Le mail est parti", SECRET], ["Tu as un rendez-vous demain"])

    code, out, err = run_main(leak, capsys, "--config", str(config))

    assert code == 0 and err == ""
    assert "développement : 2 phrases" in out
    assert "test interne : 1 phrases" in out
    assert "Aucun doublon exact." in out
    assert "Zorglub" not in out and "Marcel" not in out and "mail est parti" not in out


def test_an_exact_duplicate_between_sets_stops_with_counts_only(
    leak: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config = make_config(tmp_path, [SECRET, "Autre"], [SECRET.upper()])

    code, out, _ = run_main(leak, capsys, "--config", str(config))

    assert code == 1
    assert "1 doublon(s) exact(s)" in out
    assert "ARRÊT" in out
    assert "Zorglub" not in out.title() and "zorglub" not in out.lower()


def test_near_duplicates_are_reported_but_do_not_stop(
    leak: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config = make_config(
        tmp_path,
        ["Le mail pour Sam est bien parti ce matin"],
        ["Le mail pour Sam est bien parti hier matin"],
    )

    code, out, _ = run_main(leak, capsys, "--config", str(config), "--threshold", "0.5")

    assert code == 0
    assert "0 doublon(s) exact(s), 1 quasi-doublon(s)" in out


def test_an_external_set_is_compared_with_both_others(
    leak: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config = make_config(tmp_path, ["Le mail est parti"], ["Tu as un rendez-vous demain"])
    external = tmp_path / "externe" / "aveugle.yaml"
    external.parent.mkdir()
    external.write_text(
        HEADER + '  - reply: "Tu as un rendez-vous demain"\n    expected: []\n', encoding="utf-8"
    )

    code, out, _ = run_main(leak, capsys, "--config", str(config), "--external", str(external))

    assert code == 1
    assert "externe : 1 phrases" in out
    assert "phrases de « externe » présentes dans « test interne » : 1 doublon(s)" in out
    assert "phrases de « externe » présentes dans « développement » : 0 doublon(s)" in out


@pytest.mark.parametrize("threshold", ["0", "1.5", "-0.2"])
def test_an_invalid_threshold_is_refused(
    leak: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path, threshold: str
) -> None:
    config = make_config(tmp_path, ["a"], ["b"])

    code, _, err = run_main(leak, capsys, "--config", str(config), "--threshold", threshold)

    assert code == 1
    assert "seuil" in err


@pytest.mark.parametrize(
    "content",
    ["", "::: [", "domains: {}\n", "cases: []\n", f"cases:\n  - reply: {SECRET!r}\n    bad\n"],
)
def test_unusable_files_are_refused_without_quoting_their_content(
    leak: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path, content: str
) -> None:
    config = make_config(tmp_path, ["a"], ["b"])
    (tmp_path / "secret.yaml").write_text(content, encoding="utf-8")

    code, out, err = run_main(leak, capsys, "--config", str(config))

    assert code == 1 and out == ""
    assert "Fichier de cas" in err
    assert "Zorglub" not in err


def test_a_missing_external_file_is_refused(
    leak: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    config = make_config(tmp_path, ["a"], ["b"])

    code, _, err = run_main(
        leak, capsys, "--config", str(config), "--external", str(tmp_path / "absent.yaml")
    )

    assert code == 1
    assert "absent.yaml" in err


def test_a_missing_configuration_is_refused(
    leak: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    code, _, err = run_main(leak, capsys, "--config", str(tmp_path / "absent.yaml"))

    assert code == 1
    assert "Configuration inutilisable" in err


def test_the_repository_sets_have_no_exact_duplicate(
    leak: ModuleType, capsys: pytest.CaptureFixture[str]
) -> None:
    """Seuil d'arrêt du § 4.3 : zéro doublon exact entre développement et test interne."""
    code, out, err = run_main(leak, capsys)

    assert err == ""
    assert code == 0, out


# --- Étalonnage : des doublons plantés doivent être vus, des phrases différentes non ----------

PLANTED_EXACT = [
    ("Le mail est parti.", "LE MAIL EST PARTI"),
    ("J'ai ajouté l'événement à ton agenda.", "j’ai ajoute l evenement a ton agenda !!"),
    ("La tâche acheter du pain est ajoutée.", "  La  tâche   acheter du pain est ajoutée  "),
]
# Un mot ajouté, retiré ou changé dans une phrase de 7 mots ou plus.
PLANTED_NEAR = [
    ("Le mail pour Sam est bien parti ce matin.", "Le mail pour Sam est bien parti hier matin."),
    (
        "J'ai ajouté le rendez-vous chez le dentiste à ton agenda.",
        "J'ai ajouté le rendez-vous chez le dentiste dans ton agenda.",
    ),
    ("La tâche acheter du pain est ajoutée.", "La tâche acheter du pain est bien ajoutée."),
    (
        "J'ai supprimé la tâche appeler Paul de ta liste.",
        "J'ai supprimé la tâche appeler Paul de la liste.",
    ),
    (
        "Ton rendez-vous de demain à neuf heures est déplacé.",
        "Ton rendez-vous de demain à dix heures est déplacé.",
    ),
    ("Le mail est parti.", "Le mail est bien parti."),
]
# Phrases différentes, y compris de même gabarit mais sur un autre sujet.
PLANTED_DIFFERENT = [
    ("Le mail est parti.", "Le rendez-vous est annulé."),
    ("Tu as un événement demain.", "Tu as un mail demain."),
    ("Je n'ai pas envoyé le mail.", "J'ai envoyé le mail."),
    ("La tâche est ajoutée.", "Le rendez-vous est ajouté."),
    ("Peux-tu me dire quand est ton rendez-vous ?", "Quand veux-tu que j'envoie le mail ?"),
    ("Voici la liste de tes tâches du jour.", "Voici les mails que tu as reçus aujourd'hui."),
]


@pytest.mark.parametrize(("original", "variant"), PLANTED_EXACT)
def test_planted_exact_duplicates_are_found_whatever_the_typography(
    leak: ModuleType, original: str, variant: str
) -> None:
    result = leak.overlap([variant], [original])

    assert (result.exact, result.near) == (1, 0)


@pytest.mark.parametrize(("original", "variant"), PLANTED_NEAR)
def test_planted_near_duplicates_are_found_at_the_default_threshold(
    leak: ModuleType, original: str, variant: str
) -> None:
    result = leak.overlap([variant], [original])

    assert (result.exact, result.near) == (0, 1)
    assert leak.overlap([original], [variant]).near == 1  # dans les deux sens


@pytest.mark.parametrize(("left", "right"), PLANTED_DIFFERENT)
def test_different_sentences_are_not_reported(leak: ModuleType, left: str, right: str) -> None:
    result = leak.overlap([left], [right])

    assert (result.exact, result.near) == (0, 0)
    assert leak.overlap([right], [left]) == result


def test_calibration_through_the_command_with_exact_counts(
    leak: ModuleType, capsys: pytest.CaptureFixture[str], tmp_path: Path
) -> None:
    """Un jeu rassemble tout ce qui est planté : le rapport doit donner exactement les effectifs."""
    originals = [a for a, _ in PLANTED_EXACT + PLANTED_NEAR]
    variants = [b for _, b in PLANTED_EXACT + PLANTED_NEAR]
    others = [b for _, b in PLANTED_DIFFERENT]
    # Les phrases « différentes » doivent l'être aussi de tous les originaux.
    config = make_config(tmp_path, originals + [a for a, _ in PLANTED_DIFFERENT], variants + others)

    code, out, _ = run_main(leak, capsys, "--config", str(config))

    assert code == 1  # il y a des doublons exacts
    assert (
        f"phrases de « test interne » présentes dans « développement » : "
        f"{len(PLANTED_EXACT)} doublon(s) exact(s), {len(PLANTED_NEAR)} quasi-doublon(s)" in out
    )


def test_a_lower_threshold_widens_the_net_and_a_higher_one_narrows_it(leak: ModuleType) -> None:
    original, variant = "Tu as un événement demain.", "Tu as un mail demain."

    assert leak.overlap([variant], [original], 0.5).near == 1
    assert leak.overlap([variant], [original], 0.75).near == 0


def test_default_threshold_sits_between_the_planted_populations(leak: ModuleType) -> None:
    def similarity(left: str, right: str) -> float:
        return float(
            leak.jaccard(leak.words(leak.normalize(left)), leak.words(leak.normalize(right)))
        )

    near = [similarity(a, b) for a, b in PLANTED_NEAR]
    different = [similarity(a, b) for a, b in PLANTED_DIFFERENT]

    assert min(near) >= leak.DEFAULT_THRESHOLD
    assert max(different) < leak.DEFAULT_THRESHOLD
