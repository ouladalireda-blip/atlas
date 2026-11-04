"""Mesure le vérificateur sur un jeu de phrases annotées.

Un cas est réussi si les effets détectés sont exactement les effets attendus. Le taux de
détection porte sur les cas qui affirment une action, le taux de fausses alertes sur les autres.
"""

import io
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from atlas.core.verifier import detect_claims

DEFAULT_CASES = Path(__file__).with_name("verifier_cases.yaml")


class EvalCase(BaseModel):
    """Phrase annotée ; expected est obligatoire pour qu'un oubli ne vaille pas « aucun »."""

    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1)
    expected: list[str | None]
    # Les phrases écrites avant une règle ("base") et celles qui la testent ("variantes") se
    # mesurent séparément : on ne juge pas une règle sur les phrases qui l'ont inspirée.
    group: str = Field(default="base", min_length=1)


class EvalFile(BaseModel):
    """Contenu du fichier de cas."""

    model_config = ConfigDict(extra="forbid")

    vocabulary: dict[str, list[str]]
    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def check_expected_effects(self) -> Self:
        """Refuse un effet attendu absent du vocabulaire : il ne pourrait jamais être détecté."""
        for case in self.cases:
            unknown = {e for e in case.expected if e is not None} - set(self.vocabulary)
            if unknown:
                raise ValueError(f"Effets hors vocabulaire {sorted(unknown)} : {case.reply!r}")
        return self


@dataclass(frozen=True)
class EvalError:
    """Cas dont le résultat diffère de l'attendu."""

    reply: str
    expected: list[str | None]
    detected: list[str | None]


@dataclass(frozen=True)
class EvalReport:
    """Compteurs de l'évaluation et liste des erreurs."""

    positives: int
    detected: int
    negatives: int
    false_alarms: int
    errors: list[EvalError]


def load_eval(path: Path) -> EvalFile:
    """Lit et valide le fichier de cas."""
    return EvalFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def _sorted_effects(effects: set[str | None]) -> list[str | None]:
    """Trie les effets ; None désigne la règle générique « c'est fait »."""
    return sorted(effects, key=str)


def groups(data: EvalFile) -> list[str]:
    """Retourne les groupes du fichier, dans l'ordre d'apparition."""
    return list(dict.fromkeys(case.group for case in data.cases))


def evaluate(data: EvalFile, group: str | None = None) -> EvalReport:
    """Passe chaque phrase au vérificateur et compare les effets détectés aux effets attendus.

    Avec group, seules les phrases de ce groupe sont mesurées.
    """
    positives = detected = negatives = false_alarms = 0
    errors: list[EvalError] = []
    for case in data.cases:
        if group is not None and case.group != group:
            continue
        expected = set(case.expected)
        found = {claim.effect for claim in detect_claims(case.reply, data.vocabulary)}
        if expected:
            positives += 1
            detected += found == expected
        else:
            negatives += 1
            false_alarms += bool(found)
        if found != expected:
            errors.append(EvalError(case.reply, _sorted_effects(expected), _sorted_effects(found)))
    return EvalReport(positives, detected, negatives, false_alarms, errors)


def _rate(count: int, total: int) -> str:
    """Formate un taux ; sans cas à mesurer, aucun chiffre n'est inventé."""
    if total == 0:
        return "non mesurable (0 cas)"
    return f"{count / total:.1%} ({count}/{total})"


def format_report(report: EvalReport) -> str:
    """Rédige le rapport affiché par le script."""
    lines = [
        f"Taux de détection       : {_rate(report.detected, report.positives)}",
        f"Taux de fausses alertes : {_rate(report.false_alarms, report.negatives)}",
        f"Erreurs : {len(report.errors)}",
    ]
    for error in report.errors:
        lines.append(f"- {error.reply!r}")
        lines.append(f"    attendu : {error.expected} ; détecté : {error.detected}")
    return "\n".join(lines)


def main(arguments: list[str]) -> int:
    """Lance l'évaluation ; retourne 1 si le fichier de cas est inutilisable."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        # Sous Windows, une sortie redirigée n'accepte pas tous les caractères par défaut.
        sys.stdout.reconfigure(encoding="utf-8")
    path = Path(arguments[0]) if arguments else DEFAULT_CASES
    try:
        data = load_eval(path)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        print(f"Fichier de cas inutilisable ({path}) : {error}", file=sys.stderr)
        return 1
    names = groups(data)
    if len(names) > 1:
        for name in names:
            print(f"== Groupe « {name} » ==")
            print(format_report(evaluate(data, name)))
            print()
        print("== Total ==")
    print(format_report(evaluate(data)))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
