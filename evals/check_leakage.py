"""Contrôle de fuite entre les jeux : doublons exacts et quasi-doublons, en effectifs seulement.

    uv run python evals/check_leakage.py                          # développement et test interne
    uv run python evals/check_leakage.py --external D:\\aveugle.yaml   # + un jeu hors du dépôt
    uv run python evals/check_leakage.py --threshold 0.6          # quasi-doublons plus larges

Compare les phrases du développement, du test interne et d'un fichier externe facultatif (le jeu
aveugle). Une phrase est un doublon exact si sa forme normalisée (minuscules, sans accents ni
ponctuation, espaces réduits) est celle d'une phrase de l'autre jeu ; un quasi-doublon si la
similarité de Jaccard de leurs ensembles de mots atteint le seuil (0,75 par défaut) sans qu'elle
soit identique. Le seuil a été étalonné sur des paires plantées (tests/test_check_leakage.py) :
un mot ajouté, retiré ou changé dans une phrase de 7 mots ou plus reste au-dessus ; deux phrases
de même gabarit qui parlent d'autre chose (« tu as un événement demain » / « tu as un mail
demain ») restent en dessous. Des bigrammes de mots, essayés d'abord, ratent ces variantes courtes.

Ce script n'affiche JAMAIS une phrase, ni un indice qui permette de la retrouver (numéro de ligne,
extrait) : seulement des effectifs (docs/design/0001, § 4.3 ; docs/adr/0004). Il ne s'en sert pas
pour modifier quoi que ce soit.

Code de sortie : 1 s'il existe au moins un doublon exact entre deux jeux (seuil d'arrêt du § 4.3)
ou si un fichier est inutilisable ; 0 sinon. Les quasi-doublons sont à examiner par un humain, sans
citation, et ne font pas échouer le script.
"""

import argparse
import io
import re
import sys
import unicodedata
from dataclasses import dataclass
from itertools import combinations
from pathlib import Path

import yaml
from pydantic import ValidationError

EVALS = Path(__file__).parent
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import run_verifier_eval as runner  # noqa: E402

DEFAULT_THRESHOLD = 0.75
_NON_WORD = re.compile(r"[^0-9a-z]+")


class LeakageError(Exception):
    """Fichier inutilisable ; le message ne cite aucune phrase."""


def normalize(text: str) -> str:
    """Forme comparable d'une phrase : minuscules, sans accents, sans ponctuation."""
    decomposed = unicodedata.normalize("NFKD", text.replace("’", "'")).casefold()
    plain = "".join(char for char in decomposed if not unicodedata.combining(char))
    return _NON_WORD.sub(" ", plain).strip()


def words(normalized: str) -> frozenset[str]:
    """Ensemble des mots d'une phrase normalisée."""
    return frozenset(normalized.split())


def jaccard(left: frozenset[str], right: frozenset[str]) -> float:
    """Similarité de Jaccard de deux ensembles ; 0 si les deux sont vides."""
    union = left | right
    return len(left & right) / len(union) if union else 0.0


@dataclass(frozen=True)
class Overlap:
    """Chevauchement de deux jeux, du point de vue des phrases du premier."""

    exact: int
    near: int


def overlap(first: list[str], second: list[str], threshold: float = DEFAULT_THRESHOLD) -> Overlap:
    """Compte les phrases de `first` qui ont un doublon exact, ou un quasi-doublon, dans `second`.

    Une phrase est comptée une seule fois, et en doublon exact de préférence.
    """
    exact_forms = {normalize(text) for text in second}
    second_words = [words(form) for form in exact_forms]
    exact = near = 0
    for text in first:
        form = normalize(text)
        if form in exact_forms:
            exact += 1
        elif any(jaccard(words(form), other) >= threshold for other in second_words):
            near += 1
    return Overlap(exact, near)


def internal_duplicates(replies: list[str]) -> int:
    """Nombre de phrases d'un jeu qui répètent une phrase déjà présente (forme normalisée)."""
    return len(replies) - len({normalize(text) for text in replies})


def read_replies(path: Path) -> list[str]:
    """Lit les phrases d'un fichier de cas sans valider le reste ; ne cite rien en cas d'erreur."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        cases = data["cases"]
        replies = [str(case["reply"]) for case in cases]
    except (OSError, yaml.YAMLError, KeyError, TypeError) as error:
        raise LeakageError(
            f"Fichier de cas inutilisable ({path.name}) : détail masqué ({type(error).__name__})"
        ) from error
    if not replies:
        raise LeakageError(f"Fichier de cas vide ({path.name}).")
    return replies


def read_set(paths: list[Path]) -> list[str]:
    """Rassemble les phrases de plusieurs fichiers."""
    return [reply for path in paths for reply in read_replies(path)]


def format_report(sets: dict[str, list[str]], threshold: float) -> tuple[str, bool]:
    """Rédige le rapport (effectifs seulement) ; le booléen vaut True s'il y a un doublon exact."""
    lines = ["== Jeux =="]
    for name, replies in sets.items():
        lines.append(
            f"  {name} : {len(replies)} phrases, {internal_duplicates(replies)} répétition(s) "
            "à l'intérieur du jeu"
        )
    lines.append(f"== Chevauchements (quasi-doublon : Jaccard des mots ≥ {threshold:.2f}) ==")
    any_exact = False
    for (left_name, left), (right_name, right) in combinations(sets.items(), 2):
        for (a_name, a), (b_name, b) in (
            ((left_name, left), (right_name, right)),
            ((right_name, right), (left_name, left)),
        ):
            result = overlap(a, b, threshold)
            any_exact |= result.exact > 0
            lines.append(
                f"  phrases de « {a_name} » présentes dans « {b_name} » : "
                f"{result.exact} doublon(s) exact(s), {result.near} quasi-doublon(s)"
            )
    verdict = "ARRÊT : doublon exact entre deux jeux." if any_exact else "Aucun doublon exact."
    lines.append(verdict)
    return "\n".join(lines), any_exact


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="check_leakage.py", description="Contrôle de fuite entre les jeux."
    )
    parser.add_argument("--external", type=Path, help="fichier de cas hors du dépôt (jeu aveugle)")
    parser.add_argument("--threshold", type=float, default=DEFAULT_THRESHOLD)
    parser.add_argument("--config", type=Path, default=runner.DEFAULT_CONFIG)
    return parser


def main(arguments: list[str]) -> int:
    """Lance le contrôle ; retourne 1 sur un doublon exact ou un fichier inutilisable."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        sys.stdout.reconfigure(encoding="utf-8")
    options = _parser().parse_args(arguments)
    if not 0 < options.threshold <= 1:
        print("Le seuil doit être dans ]0 ; 1].", file=sys.stderr)
        return 1
    try:
        config = runner.load_config(options.config)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        print(f"Configuration inutilisable ({options.config.name}) : {error}", file=sys.stderr)
        return 1
    base = options.config.parent
    try:
        sets = {
            "développement": read_set([base / file for file in config.sets["dev"]]),
            "test interne": read_set([base / file for file in config.sets["test"]]),
        }
        if options.external is not None:
            sets["externe"] = read_replies(options.external)
    except LeakageError as error:
        print(str(error), file=sys.stderr)
        return 1
    report, any_exact = format_report(sets, options.threshold)
    print(report)
    return 1 if any_exact else 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
