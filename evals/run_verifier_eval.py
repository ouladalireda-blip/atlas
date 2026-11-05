"""Mesure le vérificateur sur des jeux de phrases annotées.

    uv run python evals/run_verifier_eval.py                  # développement, avec les erreurs
    uv run python evals/run_verifier_eval.py --test           # test : chiffres agrégés seulement
    uv run python evals/run_verifier_eval.py --check          # planchers du développement
    uv run python evals/run_verifier_eval.py --check --test   # planchers du test
    uv run python evals/run_verifier_eval.py chemin.yaml      # un fichier précis
    uv run python evals/run_verifier_eval.py --test D:\aveugle.yaml   # jeu hors du dépôt, agrégats

Un cas est réussi si les effets détectés sont exactement les effets attendus. Le taux de détection
porte sur les cas qui affirment une action, le taux de fausses alertes sur les autres. Par effet :
la détection compte les phrases qui attendent cet effet et où il est trouvé ; les fausses alertes
comptent les phrases qui ne l'attendent pas et où il est pourtant trouvé.

Le jeu de test ne sert qu'à mesurer : ce script n'en affiche jamais les phrases (voir
docs/adr/0004-jeu-de-test-cache.md).
"""

import argparse
import io
import math
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Self

import yaml
from pydantic import BaseModel, ConfigDict, Field, ValidationError, model_validator

from atlas.core.lexicon import VERB_CLASSES, Domain
from atlas.core.verifier import Claim, detect_claims

EVALS = Path(__file__).parent
DEFAULT_CASES = EVALS / "verifier_cases.yaml"
DEFAULT_CONFIG = EVALS / "thresholds.yaml"
# Quel que soit le chemin ou la configuration, un fichier de ce nom n'est jamais détaillé.
TEST_FILENAME = "verifier_test.yaml"
# Décimales gardées dans les planchers : une phrase pèse plus de 0,5 % sur nos effectifs.
FLOOR_DIGITS = 4
# Sous ce nombre de phrases (positives ou négatives), un taux ne dit presque rien : une phrase
# pèse plus de 10 points. Le rapport l'indique à côté du chiffre.
MIN_SAMPLE = 10


class EvalCase(BaseModel):
    """Phrase annotée ; expected est obligatoire pour qu'un oubli ne vaille pas « aucun »."""

    model_config = ConfigDict(extra="forbid")

    reply: str = Field(min_length=1)
    expected: list[str | None]
    # Les phrases écrites avant une règle ("base") et celles qui la testent ("variantes") se
    # mesurent séparément : on ne juge pas une règle sur les phrases qui l'ont inspirée.
    group: str = Field(default="base", min_length=1)


class DomainSpec(BaseModel):
    """Un domaine du fichier d'éval : ses noms et l'effet de chaque classe de verbe."""

    model_config = ConfigDict(extra="forbid")

    nouns: list[str] = Field(min_length=1)
    actions: dict[str, str] = Field(min_length=1)

    @model_validator(mode="after")
    def check_classes(self) -> Self:
        """Refuse une classe de verbes que le cœur ne connaît pas."""
        unknown = sorted(set(self.actions) - VERB_CLASSES)
        if unknown:
            raise ValueError(f"classes de verbes inconnues : {', '.join(unknown)}")
        return self


class EvalFile(BaseModel):
    """Contenu du fichier de cas : les domaines (noms et actions) puis les phrases."""

    model_config = ConfigDict(extra="forbid")

    domains: dict[str, DomainSpec]
    cases: list[EvalCase] = Field(min_length=1)

    @model_validator(mode="after")
    def check_expected_effects(self) -> Self:
        """Refuse un effet attendu qu'aucun domaine ne pourrait produire."""
        possible = set(self.declared_effects)
        possible |= {f"{name}.{verb}" for name in self.domains for verb in VERB_CLASSES}
        for case in self.cases:
            unknown = {e for e in case.expected if e is not None} - possible
            if unknown:
                raise ValueError(f"Effets hors domaines {sorted(unknown)} : {case.reply!r}")
        return self

    @property
    def declared_effects(self) -> list[str]:
        """Les effets que les domaines déclarent, sans doublon, dans l'ordre du fichier."""
        return list(
            dict.fromkeys(
                effect for domain in self.domains.values() for effect in domain.actions.values()
            )
        )

    def to_domains(self) -> dict[str, Domain]:
        """Convertit les domaines au format du vérificateur."""
        return {
            name: Domain(nouns=tuple(spec.nouns), actions=dict(spec.actions))
            for name, spec in self.domains.items()
        }


class Floor(BaseModel):
    """Seuils d'un jeu : détection minimale, fausses alertes maximales (taux entre 0 et 1)."""

    model_config = ConfigDict(extra="forbid")

    detection: float = Field(ge=0, le=1)
    false_alarms: float = Field(ge=0, le=1)


class SetFloors(Floor):
    """Planchers d'un jeu : globaux, puis par effet."""

    effects: dict[str, Floor] = Field(default_factory=dict)


class EvalConfig(BaseModel):
    """Configuration des jeux (fichiers) et de leurs planchers."""

    model_config = ConfigDict(extra="forbid")

    sets: dict[str, list[str]]
    floors: dict[str, SetFloors] = Field(default_factory=dict)

    @model_validator(mode="after")
    def check_sets(self) -> Self:
        """Exige exactement les jeux « dev » et « test », chacun avec au moins un fichier."""
        if set(self.sets) != {"dev", "test"}:
            raise ValueError("sets doit définir exactement « dev » et « test ».")
        if any(not files for files in self.sets.values()):
            raise ValueError("chaque jeu doit lister au moins un fichier.")
        if set(self.floors) - set(self.sets):
            raise ValueError("floors ne peut concerner que « dev » et « test ».")
        return self


@dataclass(frozen=True)
class EvalError:
    """Cas dont le résultat diffère de l'attendu."""

    reply: str
    expected: list[str | None]
    detected: list[str | None]


@dataclass(frozen=True)
class EffectStats:
    """Compteurs d'un effet."""

    positives: int = 0
    detected: int = 0
    negatives: int = 0
    false_alarms: int = 0

    def __add__(self, other: "EffectStats") -> "EffectStats":
        return EffectStats(
            self.positives + other.positives,
            self.detected + other.detected,
            self.negatives + other.negatives,
            self.false_alarms + other.false_alarms,
        )


@dataclass(frozen=True)
class EvalReport:
    """Compteurs de l'évaluation, détail par effet et liste des erreurs."""

    positives: int
    detected: int
    negatives: int
    false_alarms: int
    errors: list[EvalError]
    effects: dict[str, EffectStats] = field(default_factory=dict)


def load_eval(path: Path) -> EvalFile:
    """Lit et valide le fichier de cas."""
    return EvalFile.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def load_config(path: Path) -> EvalConfig:
    """Lit et valide la configuration des jeux et des planchers."""
    return EvalConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")))


def groups(data: EvalFile) -> list[str]:
    """Retourne les groupes du fichier, dans l'ordre d'apparition."""
    return list(dict.fromkeys(case.group for case in data.cases))


def _sorted_effects(effects: set[str | None]) -> list[str | None]:
    """Trie les effets ; None désigne la règle générique « c'est fait »."""
    return sorted(effects, key=str)


def _found_effects(claims: list[Claim], expected: set[str | None]) -> set[str | None]:
    """Effets trouvés dans une phrase, tels que l'annotation peut les comparer.

    Une affirmation générique de classe (« c'est ajouté », sans objet) est prouvée par un reçu de
    n'importe lequel de ses effets : elle est donc comptée pour les effets attendus qu'elle couvre,
    et comme effet « *.classe » (fausse alerte) quand l'annotation n'en attend aucun.
    """
    found: set[str | None] = set()
    for claim in claims:
        covered = expected & set(claim.any_of)
        if claim.any_of and covered:
            found |= covered
        else:
            found.add(claim.effect)
    return found


def evaluate(data: EvalFile, group: str | None = None) -> EvalReport:
    """Passe chaque phrase au vérificateur et compare les effets détectés aux effets attendus.

    Avec group, seules les phrases de ce groupe sont mesurées.
    """
    positives = detected = negatives = false_alarms = 0
    errors: list[EvalError] = []
    domains = data.to_domains()
    per_effect = {effect: EffectStats() for effect in data.declared_effects}
    for case in data.cases:
        if group is not None and case.group != group:
            continue
        expected = set(case.expected)
        found = _found_effects(detect_claims(case.reply, domains), expected)
        if expected:
            positives += 1
            detected += found == expected
        else:
            negatives += 1
            false_alarms += bool(found)
        if found != expected:
            errors.append(EvalError(case.reply, _sorted_effects(expected), _sorted_effects(found)))
        for effect in per_effect:
            wanted, seen = effect in expected, effect in found
            per_effect[effect] += EffectStats(
                positives=int(wanted),
                detected=int(wanted and seen),
                negatives=int(not wanted),
                false_alarms=int(not wanted and seen),
            )
    return EvalReport(positives, detected, negatives, false_alarms, errors, per_effect)


def combine(reports: list[EvalReport]) -> EvalReport:
    """Additionne plusieurs rapports (un jeu peut tenir en plusieurs fichiers)."""
    effects: dict[str, EffectStats] = {}
    for report in reports:
        for effect, stats in report.effects.items():
            effects[effect] = effects.get(effect, EffectStats()) + stats
    return EvalReport(
        positives=sum(report.positives for report in reports),
        detected=sum(report.detected for report in reports),
        negatives=sum(report.negatives for report in reports),
        false_alarms=sum(report.false_alarms for report in reports),
        errors=[error for report in reports for error in report.errors],
        effects=effects,
    )


def evaluate_set(config: EvalConfig, name: str, base: Path) -> EvalReport:
    """Mesure un jeu de la configuration (fichiers relatifs au dossier de la configuration)."""
    return combine([evaluate(load_eval(base / file)) for file in config.sets[name]])


def _rate(count: int, total: int) -> str:
    """Formate un taux avec ses effectifs (x/n) ; sans cas, aucun chiffre n'est inventé.

    Une catégorie de moins de MIN_SAMPLE phrases est marquée « effectif insuffisant ».
    """
    figure = "non mesurable" if total == 0 else f"{count / total:.1%}"
    text = f"{figure} ({count}/{total})"
    return f"{text} [effectif insuffisant]" if total < MIN_SAMPLE else text


def _ratio(count: int, total: int) -> float | None:
    return None if total == 0 else count / total


def format_report(report: EvalReport) -> str:
    """Rédige le rapport détaillé : taux globaux, erreurs et phrases en erreur."""
    lines = [
        f"Taux de détection       : {_rate(report.detected, report.positives)}",
        f"Taux de fausses alertes : {_rate(report.false_alarms, report.negatives)}",
        f"Erreurs : {len(report.errors)}",
    ]
    for error in report.errors:
        lines.append(f"- {error.reply!r}")
        lines.append(f"    attendu : {error.expected} ; détecté : {error.detected}")
    return "\n".join(lines)


def format_aggregate(report: EvalReport, title: str) -> str:
    """Rédige le rapport agrégé : uniquement des chiffres, jamais une phrase."""
    lines = [
        f"== {title} : {report.positives + report.negatives} phrases ==",
        f"Taux de détection       : {_rate(report.detected, report.positives)}",
        f"Taux de fausses alertes : {_rate(report.false_alarms, report.negatives)}",
        "Par effet :",
    ]
    for effect, stats in report.effects.items():
        lines.append(
            f"  {effect:<15} détection {_rate(stats.detected, stats.positives)} ; "
            f"fausses alertes {_rate(stats.false_alarms, stats.negatives)}"
        )
    return "\n".join(lines)


def _floor_violations(
    label: str, detected: float | None, alarms: float | None, floor: Floor
) -> list[str]:
    problems: list[str] = []
    if detected is not None and detected + 1e-9 < floor.detection:
        problems.append(
            f"{label} : détection {detected:.1%} sous le plancher {floor.detection:.1%}"
        )
    if alarms is not None and alarms - 1e-9 > floor.false_alarms:
        problems.append(
            f"{label} : fausses alertes {alarms:.1%} au-dessus du plafond {floor.false_alarms:.1%}"
        )
    return problems


def check_floors(report: EvalReport, floors: SetFloors) -> list[str]:
    """Compare un rapport aux planchers ; retourne les dépassements, avec des chiffres seulement."""
    problems = _floor_violations(
        "global",
        _ratio(report.detected, report.positives),
        _ratio(report.false_alarms, report.negatives),
        floors,
    )
    for effect, floor in floors.effects.items():
        stats = report.effects.get(effect)
        if stats is None:
            problems.append(f"{effect} : effet absent de la mesure")
            continue
        problems += _floor_violations(
            effect,
            _ratio(stats.detected, stats.positives),
            _ratio(stats.false_alarms, stats.negatives),
            floor,
        )
    return problems


def current_floors(report: EvalReport) -> SetFloors:
    """Calcule les planchers au niveau actuel, arrondis du côté qui ne durcit pas le seuil."""

    def floor_of(stats: EffectStats | EvalReport) -> Floor:
        scale = 10**FLOOR_DIGITS
        detection = (stats.detected / stats.positives) if stats.positives else 0.0
        alarms = (stats.false_alarms / stats.negatives) if stats.negatives else 1.0
        return Floor(
            detection=math.floor(detection * scale) / scale,
            false_alarms=min(1.0, math.ceil(alarms * scale) / scale),
        )

    overall = floor_of(report)
    return SetFloors(
        detection=overall.detection,
        false_alarms=overall.false_alarms,
        effects={effect: floor_of(stats) for effect, stats in report.effects.items()},
    )


def is_hidden(path: Path, config: EvalConfig | None, base: Path) -> bool:
    """Indique si un fichier est le jeu de test : ses phrases ne s'affichent jamais."""
    if path.name == TEST_FILENAME:
        return True
    if config is None:
        return False
    hidden = {(base / file).resolve() for file in config.sets["test"]}
    return path.resolve() in hidden


def _describe(error: Exception, hidden: bool) -> str:
    """Décrit une erreur de lecture ; pour le jeu de test, le détail peut citer une phrase."""
    return f"détail masqué ({type(error).__name__})" if hidden else str(error)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="run_verifier_eval.py", description="Mesure le vérificateur."
    )
    parser.add_argument("files", nargs="*", type=Path, help="fichiers de cas (sinon : le jeu dev)")
    parser.add_argument(
        "--test",
        action="store_true",
        help="mesure un jeu de test (agrégats), avec ou sans fichiers",
    )
    parser.add_argument("--check", action="store_true", help="échoue sous les planchers")
    parser.add_argument("--write-floors", action="store_true", help="écrit les niveaux actuels")
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    return parser


def _write_floors(config_path: Path, config: EvalConfig) -> None:
    base = config_path.parent
    floors = {
        name: current_floors(evaluate_set(config, name, base)).model_dump()
        for name in ("dev", "test")
    }
    header = (
        "# Jeux d'évaluation du vérificateur et planchers de la CI (taux entre 0 et 1).\n"
        "# Les planchers sont les niveaux mesurés : detection = minimum, false_alarms = maximum.\n"
        "# Les modifier est une décision à justifier : jamais pour faire passer la CI.\n"
        "# Régénérés par : uv run python evals/run_verifier_eval.py --write-floors\n\n"
    )
    body = yaml.safe_dump(
        {"sets": config.sets, "floors": floors}, allow_unicode=True, sort_keys=False, width=100
    )
    config_path.write_text(header + body, encoding="utf-8")


def _run_files(options: argparse.Namespace, config: EvalConfig | None) -> int:
    """Mesure des fichiers précis, dans le détail sauf pour le jeu de test."""
    base = options.config.parent
    for path in options.files:
        try:
            data = load_eval(path)
        except (OSError, yaml.YAMLError, ValidationError) as error:
            detail = _describe(error, is_hidden(path, config, base))
            print(f"Fichier de cas inutilisable ({path}) : {detail}", file=sys.stderr)
            return 1
        if is_hidden(path, config, base):
            print(format_aggregate(evaluate(data), "test"))
            continue
        names = groups(data)
        if len(names) > 1:
            for name in names:
                print(f"== Groupe « {name} » ==")
                print(format_report(evaluate(data, name)))
                print()
            print("== Total ==")
        print(format_report(evaluate(data)))
    return 0


def _run_external_test(options: argparse.Namespace) -> int:
    """Mesure un ou plusieurs fichiers comme un jeu de test, où qu'ils soient (hors du dépôt).

    Les phrases ne s'affichent jamais, quel que soit le nom du fichier, et une erreur de lecture
    n'est signalée que par son type.
    """
    reports: list[EvalReport] = []
    for path in options.files:
        try:
            reports.append(evaluate(load_eval(path)))
        except (OSError, yaml.YAMLError, ValidationError) as error:
            detail = _describe(error, hidden=True)
            print(f"Fichier de cas inutilisable ({path.name}) : {detail}", file=sys.stderr)
            return 1
    print(format_aggregate(combine(reports), "test externe"))
    return 0


def main(arguments: list[str]) -> int:
    """Lance l'évaluation ; retourne 1 si un fichier est inutilisable ou un plancher franchi."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        # Sous Windows, une sortie redirigée n'accepte pas tous les caractères par défaut.
        sys.stdout.reconfigure(encoding="utf-8")
    options = _parser().parse_args(arguments)
    config: EvalConfig | None = None
    try:
        config = load_config(options.config)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        # Des fichiers précis se mesurent sans configuration (par exemple un jeu hors du dépôt).
        if not options.files:
            print(f"Configuration inutilisable ({options.config}) : {error}", file=sys.stderr)
            return 1
    if options.files:
        if options.check or options.write_floors:
            print(
                "Des fichiers précis ne se combinent pas avec --check ni --write-floors.",
                file=sys.stderr,
            )
            return 1
        return _run_external_test(options) if options.test else _run_files(options, config)
    assert config is not None
    base = options.config.parent
    try:
        if options.write_floors:
            _write_floors(options.config, config)
            print(f"Planchers écrits dans {options.config}.")
            return 0
        name = "test" if options.test else "dev"
        report = evaluate_set(config, name, base)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        detail = _describe(error, options.test or options.write_floors)
        print(f"Fichier de cas inutilisable : {detail}", file=sys.stderr)
        return 1
    print(format_aggregate(report, name))
    if name == "dev":
        print()
        print(format_report(report))  # le détail des erreurs n'existe que pour le développement
    if options.check:
        floors = config.floors.get(name)
        if floors is None:
            print(f"Aucun plancher défini pour le jeu « {name} ».", file=sys.stderr)
            return 1
        problems = check_floors(report, floors)
        for problem in problems:
            print(f"PLANCHER FRANCHI - {problem}", file=sys.stderr)
        print(f"Planchers {name} : {'franchis' if problems else 'respectés'}.")
        return 1 if problems else 0
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
