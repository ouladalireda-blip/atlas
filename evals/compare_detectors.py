"""Compare des détecteurs d'affirmations sur les mêmes jeux (docs/design/0001, §§ 4 à 6).

    uv run python evals/compare_detectors.py                          # R sur le développement
    uv run python evals/compare_detectors.py --detectors rules,spacy  # plusieurs candidats
    uv run python evals/compare_detectors.py --test                   # test interne : agrégats
    uv run python evals/compare_detectors.py --test D:\\aveugle.yaml    # jeu hors du dépôt
    uv run python evals/compare_detectors.py --mlflow                 # un run MLflow par mesure

Pour chaque (détecteur, jeu) : détection et fausses alertes, globales et par effet, avec leurs
effectifs (x/n) et leur intervalle de Wilson à 95 %, puis la latence p50/p95 par réponse. Les
comptes viennent de la fonction `evaluate` de run_verifier_eval.py : les chiffres de R sont donc
ceux de ce script par construction.

Ce script n'affiche JAMAIS une phrase, quel que soit le jeu : ni dans le terminal, ni dans MLflow
(voir docs/adr/0004-jeu-de-test-cache.md). Le mode --test masque en plus le détail des erreurs de
lecture, et ne lit que des fichiers de cas pour les mesurer.

MLflow est facultatif (groupe de dépendances « eval » : `uv sync --group eval`). La latence est
mesurée avant d'ouvrir un run MLflow, hors de son instrumentation.
"""

import argparse
import hashlib
import io
import math
import os
import platform
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml
from pydantic import ValidationError

EVALS = Path(__file__).parent
ROOT = EVALS.parent
# Ce script se lance comme un fichier : son dossier doit permettre d'importer le runner voisin.
if str(EVALS) not in sys.path:
    sys.path.insert(0, str(EVALS))

import run_verifier_eval as runner  # noqa: E402

from atlas.core.claims import (  # noqa: E402
    ClaimDetector,
    DetectorUnavailable,
    Domains,
    UnknownDetectorError,
    build_detector,
    detector_names,
)

Z_95 = 1.959964
# Protocole de latence (conception § 5.1) : des tours de chauffe sur tout le jeu, puis chaque phrase
# mesurée N fois, une réponse à la fois. 151 phrases x 5 répétitions = 755 mesures.
DEFAULT_WARMUP_ROUNDS = 1
DEFAULT_REPEATS = 5
DEFAULT_EXPERIMENT = "atlas-claim-detectors"
DEFAULT_TRACKING_URI = f"sqlite:///{(ROOT / 'mlruns' / 'mlflow.db').as_posix()}"


class CompareError(Exception):
    """Erreur d'usage ou de lecture ; le message ne contient jamais une phrase d'un jeu."""


@dataclass(frozen=True)
class Interval:
    """Intervalle de confiance d'une proportion (bornes entre 0 et 1)."""

    low: float
    high: float


def wilson(count: int, total: int, z: float = Z_95) -> Interval | None:
    """Intervalle de Wilson d'une proportion ; None sans cas (aucun chiffre n'est inventé)."""
    if total <= 0:
        return None
    p = count / total
    denominator = 1 + z * z / total
    center = (p + z * z / (2 * total)) / denominator
    half = z * math.sqrt(p * (1 - p) / total + z * z / (4 * total * total)) / denominator
    return Interval(max(0.0, center - half), min(1.0, center + half))


def percentile(sorted_values: list[float], fraction: float) -> float:
    """Percentile par rang le plus proche (méthode « nearest-rank ») d'une liste triée."""
    if not sorted_values:
        raise ValueError("Aucune valeur à résumer.")
    rank = max(1, math.ceil(fraction * len(sorted_values)))
    return sorted_values[min(rank, len(sorted_values)) - 1]


def _windows_cpu_name() -> str | None:
    """Nom commercial du processeur d'après le registre Windows."""
    if sys.platform != "win32":
        return None
    import winreg

    try:
        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"HARDWARE\DESCRIPTION\System\CentralProcessor\0"
        ) as key:
            return str(winreg.QueryValueEx(key, "ProcessorNameString")[0]).strip()
    except OSError:
        return None


def _ram_gb() -> float | None:
    """Mémoire vive installée, en Go, ou None si le système ne la donne pas."""
    try:
        if sys.platform == "win32":
            import ctypes

            class MemoryStatus(ctypes.Structure):
                _fields_ = [  # noqa: RUF012
                    ("length", ctypes.c_ulong),
                    ("load", ctypes.c_ulong),
                    ("total", ctypes.c_ulonglong),
                    ("avail", ctypes.c_ulonglong),
                    ("total_page", ctypes.c_ulonglong),
                    ("avail_page", ctypes.c_ulonglong),
                    ("total_virtual", ctypes.c_ulonglong),
                    ("avail_virtual", ctypes.c_ulonglong),
                    ("avail_extended", ctypes.c_ulonglong),
                ]

            status = MemoryStatus()
            status.length = ctypes.sizeof(MemoryStatus)
            if not ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(status)):
                return None
            return float(status.total) / 1024**3
        for line in Path("/proc/meminfo").read_text(encoding="utf-8").splitlines():
            if line.startswith("MemTotal:"):
                return int(line.split()[1]) / 1024**2
    except (OSError, ValueError, AttributeError):
        return None
    return None


def machine_description() -> str:
    """Décrit la machine de mesure : processeur, cœurs, mémoire, système et Python."""
    cpu = _windows_cpu_name() or platform.processor() or platform.machine()
    parts = [cpu, f"{os.cpu_count()} cœurs logiques"]
    ram = _ram_gb()
    if ram is not None:
        parts.append(f"{ram:.1f} Go de RAM")
    parts += [platform.platform(), f"Python {platform.python_version()}"]
    return " ; ".join(parts)


@dataclass(frozen=True)
class Latency:
    """Latence par réponse, en millisecondes, et le protocole qui l'a produite."""

    p50_ms: float
    p95_ms: float
    measures: int
    phrases: int
    repeats: int
    warmup_rounds: int
    machine: str


def measure_latency(
    detector: ClaimDetector,
    inputs: list[tuple[str, Domains]],
    *,
    warmup_rounds: int = DEFAULT_WARMUP_ROUNDS,
    repeats: int = DEFAULT_REPEATS,
) -> Latency:
    """Chronomètre le détecteur, une réponse à la fois.

    Les tours de chauffe repassent sur tout le jeu sans rien compter. Ensuite, chaque phrase est
    mesurée `repeats` fois (un tour complet du jeu par répétition) et p50/p95 portent sur
    l'ensemble des mesures. Le chargement du modèle est hors mesure : le détecteur est déjà
    construit. Le chronométrage couvre le pré-traitement, le modèle et la résolution d'effet.
    """
    if not inputs:
        raise ValueError("Aucune phrase à chronométrer.")
    if warmup_rounds < 0 or repeats < 1:
        raise ValueError("La chauffe doit être de 0 tour ou plus et les répétitions d'au moins 1.")
    for _ in range(warmup_rounds):
        for reply, domains in inputs:
            detector.detect(reply, domains)
    durations: list[float] = []
    for _ in range(repeats):
        for reply, domains in inputs:
            start = time.perf_counter_ns()
            detector.detect(reply, domains)
            durations.append((time.perf_counter_ns() - start) / 1e6)
    durations.sort()
    return Latency(
        percentile(durations, 0.5),
        percentile(durations, 0.95),
        len(durations),
        len(inputs),
        repeats,
        warmup_rounds,
        machine_description(),
    )


@dataclass(frozen=True)
class Dataset:
    """Un jeu à mesurer : son nom, ses fichiers, et s'il est caché (test, aveugle)."""

    name: str
    paths: tuple[Path, ...]
    hidden: bool


@dataclass(frozen=True)
class DatasetInfo:
    """Identité d'un jeu mesuré : empreinte SHA-256 et effectifs, jamais de phrase."""

    name: str
    files: tuple[str, ...]
    sha256: str
    phrases: int
    hidden: bool


@dataclass(frozen=True)
class Measurement:
    """Résultat d'un détecteur sur un jeu."""

    detector: str
    dataset: DatasetInfo
    report: runner.EvalReport
    latency: Latency | None


def file_sha256(path: Path) -> str:
    """Empreinte SHA-256 d'un fichier, lu par blocs."""
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def dataset_sha256(paths: tuple[Path, ...]) -> str:
    """Empreinte du jeu : celle du fichier s'il est seul, sinon celle des empreintes en ordre."""
    digests = [file_sha256(path) for path in paths]
    if len(digests) == 1:
        return digests[0]
    return hashlib.sha256("\n".join(digests).encode("ascii")).hexdigest()


def load_dataset(dataset: Dataset) -> list[runner.EvalFile]:
    """Lit les fichiers du jeu ; l'erreur ne cite pas de phrase si le jeu est caché."""
    loaded: list[runner.EvalFile] = []
    for path in dataset.paths:
        try:
            loaded.append(runner.load_eval(path))
        except (OSError, yaml.YAMLError, ValidationError) as error:
            detail = runner._describe(error, dataset.hidden)
            raise CompareError(f"Fichier de cas inutilisable ({path.name}) : {detail}") from error
    return loaded


def inputs_of(files: list[runner.EvalFile]) -> list[tuple[str, Domains]]:
    """Les (réponse, domaines) du jeu, pour chronométrer le détecteur."""
    return [(case.reply, data.to_domains()) for data in files for case in data.cases]


def measure(
    detector: ClaimDetector,
    dataset: Dataset,
    files: list[runner.EvalFile],
    *,
    latency: bool = True,
    warmup_rounds: int = DEFAULT_WARMUP_ROUNDS,
    repeats: int = DEFAULT_REPEATS,
) -> Measurement:
    """Mesure un détecteur sur un jeu déjà lu : comptes, puis latence."""
    try:
        report = runner.combine([runner.evaluate(data, None, detector) for data in files])
        timing = (
            measure_latency(
                detector, inputs_of(files), warmup_rounds=warmup_rounds, repeats=repeats
            )
            if latency
            else None
        )
    except Exception as error:
        detail = runner._describe(error, dataset.hidden)
        raise CompareError(
            f"Détecteur « {detector.name} » en échec sur le jeu « {dataset.name} » : "
            f"{type(error).__name__} ({detail})"
        ) from error
    info = DatasetInfo(
        name=dataset.name,
        files=tuple(path.name for path in dataset.paths),
        sha256=dataset_sha256(dataset.paths),
        phrases=report.positives + report.negatives,
        hidden=dataset.hidden,
    )
    return Measurement(detector.name, info, report, timing)


def format_rate(count: int, total: int) -> str:
    """Formate un taux avec ses effectifs, son intervalle de Wilson et l'alerte d'effectif."""
    interval = wilson(count, total)
    if interval is None:
        text = f"non mesurable ({count}/{total})"
    else:
        text = (
            f"{count / total:.1%} ({count}/{total}) IC95 [{interval.low:.1%} ; {interval.high:.1%}]"
        )
    return f"{text} [effectif insuffisant]" if total < runner.MIN_SAMPLE else text


def format_measurement(measurement: Measurement) -> str:
    """Rédige le rapport d'une mesure : uniquement des chiffres, jamais une phrase."""
    info, report = measurement.dataset, measurement.report
    lines = [
        f"== {measurement.detector} · {info.name} · {info.phrases} phrases · "
        f"sha256 {info.sha256[:12]} ==",
        f"  détection        {format_rate(report.detected, report.positives)}",
        f"  fausses alertes  {format_rate(report.false_alarms, report.negatives)}",
        "  par effet :",
    ]
    for effect, stats in report.effects.items():
        lines.append(f"    {effect}")
        lines.append(f"      détection        {format_rate(stats.detected, stats.positives)}")
        lines.append(f"      fausses alertes  {format_rate(stats.false_alarms, stats.negatives)}")
    if measurement.latency is None:
        lines.append("  latence : non mesurée")
    else:
        latency = measurement.latency
        lines.append(
            f"  latence par réponse : p50 {latency.p50_ms:.3f} ms ; p95 {latency.p95_ms:.3f} ms "
            f"({latency.measures} mesures = {latency.phrases} phrases x {latency.repeats} "
            f"répétitions, {latency.warmup_rounds} tour(s) de chauffe)"
        )
        lines.append(f"  machine : {latency.machine}")
    return "\n".join(lines)


def _point(count: int, total: int) -> str:
    return "n/a" if total == 0 else f"{count / total:.1%}"


def format_summary(measurements: list[Measurement]) -> str:
    """Tableau comparatif : une ligne par (détecteur, jeu), avec les estimations ponctuelles."""
    rows = [
        (
            m.detector,
            m.dataset.name,
            _point(m.report.detected, m.report.positives),
            _point(m.report.false_alarms, m.report.negatives),
            "n/a" if m.latency is None else f"{m.latency.p95_ms:.3f} ms",
        )
        for m in measurements
    ]
    headers = ("détecteur", "jeu", "détection", "fausses alertes", "latence p95")
    widths = [max(len(str(row[i])) for row in [headers, *rows]) for i in range(len(headers))]
    return "\n".join(
        "  ".join(cell.ljust(width) for cell, width in zip(line, widths, strict=True))
        for line in [headers, *rows]
    )


# --- Suivi MLflow -----------------------------------------------------------------------------


@dataclass(frozen=True)
class GitInfo:
    """Commit et état de l'arbre de travail au moment de la mesure."""

    commit: str
    dirty: bool | None


def git_info(cwd: Path = ROOT) -> GitInfo:
    """Lit le commit courant ; « inconnu » si git est absent ou le dossier n'est pas un dépôt."""

    def run(*arguments: str) -> str | None:
        try:
            done = subprocess.run(
                ["git", *arguments], cwd=cwd, capture_output=True, text=True, check=False
            )
        except OSError:
            return None
        return done.stdout.strip() if done.returncode == 0 else None

    commit = run("rev-parse", "HEAD")
    if commit is None:
        return GitInfo("inconnu", None)
    status = run("status", "--porcelain")
    return GitInfo(commit, None if status is None else bool(status))


@dataclass(frozen=True)
class RunPayload:
    """Ce qu'un run MLflow enregistre : paramètres, étiquettes, métriques et rapport agrégé."""

    name: str
    params: dict[str, str | int | float]
    tags: dict[str, str]
    metrics: dict[str, float]
    report: str


def _add_rate(metrics: dict[str, float], prefix: str, count: int, total: int) -> None:
    """Ajoute le taux et les bornes de Wilson ; sans cas, rien n'est inventé."""
    interval = wilson(count, total)
    if interval is None:
        return
    metrics[prefix] = count / total
    metrics[f"{prefix}_wilson_low"] = interval.low
    metrics[f"{prefix}_wilson_high"] = interval.high


def build_payload(measurement: Measurement, git: GitInfo) -> RunPayload:
    """Prépare l'enregistrement d'une mesure ; ne contient aucune phrase du jeu."""
    info, report = measurement.dataset, measurement.report
    metrics: dict[str, float] = {}
    _add_rate(metrics, "detection", report.detected, report.positives)
    _add_rate(metrics, "false_alarms", report.false_alarms, report.negatives)
    for effect, stats in report.effects.items():
        _add_rate(metrics, f"effect.{effect}.detection", stats.detected, stats.positives)
        _add_rate(metrics, f"effect.{effect}.false_alarms", stats.false_alarms, stats.negatives)
    params: dict[str, str | int | float] = {
        "detector": measurement.detector,
        "dataset": info.name,
        "files": ", ".join(info.files),
        "phrases": info.phrases,
        "positives": report.positives,
        "negatives": report.negatives,
        "wilson_z": Z_95,
    }
    if measurement.latency is not None:
        metrics["latency_p50_ms"] = measurement.latency.p50_ms
        metrics["latency_p95_ms"] = measurement.latency.p95_ms
        params["latency_measures"] = measurement.latency.measures
        params["latency_repeats"] = measurement.latency.repeats
        params["latency_warmup_rounds"] = measurement.latency.warmup_rounds
        params["machine"] = measurement.latency.machine
    tags = {
        "git_commit": git.commit,
        "git_dirty": "inconnu" if git.dirty is None else str(git.dirty).lower(),
        "dataset_sha256": info.sha256,
        "dataset_hidden": str(info.hidden).lower(),
        "python": platform.python_version(),
        "machine": machine_description(),
    }
    return RunPayload(
        name=f"{measurement.detector}-{info.name}",
        params=params,
        tags=tags,
        metrics=metrics,
        report=format_measurement(measurement),
    )


def log_to_mlflow(
    payloads: list[RunPayload], tracking_uri: str, experiment: str = DEFAULT_EXPERIMENT
) -> None:
    """Écrit un run MLflow par mesure ; mlflow n'est importé qu'ici (groupe de dépendances eval)."""
    os.environ.setdefault("MLFLOW_DISABLE_AGENT_HINT", "1")
    try:
        import mlflow
    except ImportError as error:
        raise CompareError(
            "MLflow n'est pas installé : lancer « uv sync --group eval » (hors CI)."
        ) from error
    mlflow.set_tracking_uri(tracking_uri)
    client: Any = mlflow.MlflowClient(tracking_uri)
    existing = client.get_experiment_by_name(experiment)
    if existing is None:
        artifacts = (ROOT / "mlruns" / "artifacts").resolve().as_uri()
        experiment_id = client.create_experiment(experiment, artifact_location=artifacts)
    else:
        experiment_id = existing.experiment_id
    for payload in payloads:
        with mlflow.start_run(experiment_id=experiment_id, run_name=payload.name):
            mlflow.log_params(payload.params)
            mlflow.set_tags(payload.tags)
            mlflow.log_metrics(payload.metrics)
            mlflow.log_text(payload.report, "rapport.txt")


# --- Ligne de commande ------------------------------------------------------------------------


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="compare_detectors.py", description="Compare des détecteurs d'affirmations."
    )
    parser.add_argument("files", nargs="*", type=Path, help="fichiers de cas (sinon : le jeu dev)")
    parser.add_argument(
        "--test",
        action="store_true",
        help="jeu de test (interne, ou les fichiers donnés) : agrégats seulement",
    )
    parser.add_argument(
        "--detectors",
        default="rules",
        help=f"détecteurs séparés par des virgules ({', '.join(detector_names())})",
    )
    parser.add_argument("--no-latency", action="store_true", help="ne mesure pas la latence")
    parser.add_argument(
        "--warmup",
        type=int,
        default=DEFAULT_WARMUP_ROUNDS,
        help="tours de chauffe sur tout le jeu avant la mesure de latence",
    )
    parser.add_argument(
        "--repeats",
        type=int,
        default=DEFAULT_REPEATS,
        help="nombre de mesures de chaque phrase pour la latence",
    )
    parser.add_argument("--mlflow", action="store_true", help="écrit un run MLflow par mesure")
    parser.add_argument("--tracking-uri", default=DEFAULT_TRACKING_URI)
    parser.add_argument("--experiment", default=DEFAULT_EXPERIMENT)
    parser.add_argument("--config", type=Path, default=runner.DEFAULT_CONFIG)
    return parser


def resolve_dataset(options: argparse.Namespace) -> Dataset:
    """Détermine le jeu à mesurer d'après les options."""
    config: runner.EvalConfig | None = None
    try:
        config = runner.load_config(options.config)
    except (OSError, yaml.YAMLError, ValidationError) as error:
        # Des fichiers précis se mesurent sans configuration (par exemple un jeu hors du dépôt).
        if not options.files:
            raise CompareError(
                f"Configuration inutilisable ({options.config}) : {error}"
            ) from error
    base = options.config.parent
    if options.files:
        hidden = options.test or any(runner.is_hidden(path, config, base) for path in options.files)
        return Dataset("externe" if options.test else "fichiers", tuple(options.files), hidden)
    assert config is not None
    name = "test" if options.test else "dev"
    return Dataset(name, tuple(base / file for file in config.sets[name]), options.test)


def _build_detectors(names: str) -> list[ClaimDetector]:
    """Construit les détecteurs demandés, sans repli : une panne doit se voir dans la mesure."""
    wanted = [name.strip() for name in names.split(",") if name.strip()]
    if not wanted:
        raise CompareError("Aucun détecteur demandé (--detectors).")
    try:
        return [build_detector(name, resilient=False) for name in dict.fromkeys(wanted)]
    except (UnknownDetectorError, DetectorUnavailable) as error:
        raise CompareError(str(error)) from error


def run(options: argparse.Namespace) -> list[Measurement]:
    """Mesure chaque détecteur sur le jeu ; la latence est prise avant tout enregistrement."""
    detectors = _build_detectors(options.detectors)
    dataset = resolve_dataset(options)
    files = load_dataset(dataset)
    return [
        measure(
            detector,
            dataset,
            files,
            latency=not options.no_latency,
            warmup_rounds=options.warmup,
            repeats=options.repeats,
        )
        for detector in detectors
    ]


def main(arguments: list[str]) -> int:
    """Lance la comparaison ; retourne 1 si une entrée est inutilisable ou un détecteur échoue."""
    if isinstance(sys.stdout, io.TextIOWrapper):
        # Sous Windows, une sortie redirigée n'accepte pas tous les caractères par défaut.
        sys.stdout.reconfigure(encoding="utf-8")
    options = _parser().parse_args(arguments)
    try:
        measurements = run(options)
        for measurement in measurements:
            print(format_measurement(measurement))
            print()
        if len(measurements) > 1:
            print(format_summary(measurements))
        if options.mlflow:
            git = git_info()
            payloads = [build_payload(m, git) for m in measurements]
            log_to_mlflow(payloads, options.tracking_uri, options.experiment)
            print(f"{len(measurements)} run(s) MLflow écrit(s) dans {options.tracking_uri}.")
    except CompareError as error:
        print(str(error), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
