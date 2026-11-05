"""Découpe le jeu indépendant en développement et test, 50/50, stratifié par effet attendu.

    uv run python evals/split_cases.py

Chaque phrase tombe dans une strate : sa combinaison exacte d'effets attendus (« calendar.write »,
« calendar.write + email.send », « aucun »…). Chaque strate est partagée par moitié, avec une graine
fixe, donc les phrases multi-effets sont réparties comme les autres. Le résultat est identique à
chaque exécution tant que le fichier source ne change pas.

Le script n'affiche jamais de phrase : uniquement des effectifs. Il refuse d'écraser des fichiers
existants qui différeraient du résultat, car regénérer le jeu de test après coup le ferait glisser
vers les phrases qu'on a déjà vues (voir docs/adr/0004-jeu-de-test-cache.md).
"""

import argparse
import random
import sys
from collections import defaultdict
from pathlib import Path
from typing import Any

import yaml

EVALS = Path(__file__).parent
SOURCE = EVALS / "independent_cases.yaml"
DEV_FILE = EVALS / "verifier_dev.yaml"
TEST_FILE = EVALS / "verifier_test.yaml"
SEED = 20261104

DEV_HEADER = """\
# Jeu de DÉVELOPPEMENT du vérificateur : moitié de evals/independent_cases.yaml.
# Généré par evals/split_cases.py (seed {seed}) : ne pas modifier à la main.
# Sert à mesurer pendant qu'on modifie le vérificateur, avec verifier_cases.yaml.
"""
TEST_HEADER = """\
# Jeu de TEST du vérificateur : autre moitié de evals/independent_cases.yaml.
# Généré par evals/split_cases.py (seed {seed}) : ne pas modifier à la main.
# NE JAMAIS LIRE NI UTILISER pour modifier le vérificateur : il ne sert qu'à mesurer.
# Le runner (--test) n'affiche que des chiffres agrégés. Voir docs/adr/0004-jeu-de-test-cache.md.
"""


class SplitError(Exception):
    """Le découpage ne peut pas être fait ou écraserait un résultat différent."""


def stratum(case: dict[str, Any]) -> tuple[str, ...]:
    """Retourne la strate d'une phrase : ses effets attendus triés, vide pour « aucun »."""
    return tuple(sorted(str(effect) for effect in case["expected"]))


def split(cases: list[dict[str, Any]], seed: int = SEED) -> tuple[list[int], list[int]]:
    """Retourne les positions des phrases du développement et celles du test.

    La moitié de chaque strate va au test ; quand l'effectif est impair, le surplus va tour à tour
    au développement et au test, pour que les deux jeux restent de taille voisine.
    """
    strata: dict[tuple[str, ...], list[int]] = defaultdict(list)
    for position, case in enumerate(cases):
        strata[stratum(case)].append(position)
    dev: list[int] = []
    test: list[int] = []
    for rank, key in enumerate(sorted(strata)):
        members = sorted(strata[key], key=lambda position: str(cases[position]["reply"]))
        random.Random(f"{seed}:{'+'.join(key)}").shuffle(members)
        in_test = len(members) // 2 + (len(members) % 2 if rank % 2 else 0)
        test.extend(members[:in_test])
        dev.extend(members[in_test:])
    return sorted(dev), sorted(test)


def render(header: str, domains: dict[str, Any], cases: list[dict[str, Any]]) -> str:
    """Rédige un fichier de cas."""
    body = yaml.safe_dump(
        {"domains": domains, "cases": cases},
        allow_unicode=True,
        sort_keys=False,
        width=100,
    )
    return header.format(seed=SEED) + "\n" + body


def load_source(path: Path) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    """Lit le jeu indépendant (domaines et cas) et contrôle sa forme."""
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError) as error:
        raise SplitError(f"Source inutilisable ({path}) : {error}") from error
    if not isinstance(data, dict) or not data.get("cases") or not data.get("domains"):
        raise SplitError(f"Source invalide ({path}) : « domains » et « cases » sont requis.")
    cases = data["cases"]
    if any("reply" not in case or "expected" not in case for case in cases):
        raise SplitError(f"Source invalide ({path}) : chaque cas a « reply » et « expected ».")
    replies = [case["reply"] for case in cases]
    if len(set(replies)) != len(replies):
        raise SplitError(f"Source invalide ({path}) : des phrases sont en double.")
    return data["domains"], cases


def write_checked(path: Path, content: str, force: bool) -> str:
    """Écrit le fichier, sauf s'il existe déjà avec un autre contenu (sans --force)."""
    if path.exists():
        if path.read_text(encoding="utf-8") == content:
            return "déjà à jour"
        if not force:
            raise SplitError(
                f"{path.name} existe déjà avec un autre contenu : refus de l'écraser. "
                "Regénérer le jeu de test après coup le fausse ; utilise --force en connaissance "
                "de cause."
            )
    path.write_text(content, encoding="utf-8")
    return "écrit"


def main(arguments: list[str]) -> int:
    """Lance le découpage ; retourne 1 en cas d'erreur."""
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0] if __doc__ else None)
    parser.add_argument("--source", type=Path, default=SOURCE)
    parser.add_argument("--dev", type=Path, default=DEV_FILE)
    parser.add_argument("--test", type=Path, default=TEST_FILE)
    parser.add_argument("--force", action="store_true", help="écrase des fichiers différents")
    options = parser.parse_args(arguments)
    try:
        domains, cases = load_source(options.source)
        dev_positions, test_positions = split(cases)
        # Contrôle de sûreté : aucune phrase perdue, aucune phrase dans les deux jeux.
        if sorted(dev_positions + test_positions) != list(range(len(cases))):
            raise SplitError("Découpage incohérent : des phrases sont perdues ou dupliquées.")
        outputs = (
            (options.dev, DEV_HEADER, dev_positions),
            (options.test, TEST_HEADER, test_positions),
        )
        for path, header, positions in outputs:
            content = render(header, domains, [cases[position] for position in positions])
            status = write_checked(path, content, options.force)
            print(f"{path.name} : {len(positions)} phrases ({status})")
    except SplitError as error:
        print(f"Découpage impossible : {error}", file=sys.stderr)
        return 1
    print("Effectifs par strate (développement / test) :")
    for key in sorted({stratum(case) for case in cases}):
        name = " + ".join(key) or "aucun"
        in_dev = sum(1 for p in dev_positions if stratum(cases[p]) == key)
        in_test = sum(1 for p in test_positions if stratum(cases[p]) == key)
        print(f"  {name} : {in_dev} / {in_test}")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
