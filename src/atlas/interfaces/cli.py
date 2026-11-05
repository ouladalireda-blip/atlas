"""Interface en ligne de commande d'ATLAS."""

import json
import logging
import sqlite3
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import date
from pathlib import Path
from typing import Annotated, Any

import typer
from pydantic import ValidationError
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from atlas import __version__
from atlas.config import Settings
from atlas.core.agent import Agent, ApprovalHandler, TurnResult, Verdict
from atlas.core.claims import DetectorUnavailable, UnknownDetectorError, build_detector
from atlas.core.receipts import Receipt, ReceiptStatus
from atlas.core.trace import Action, EventKind, Reason, TraceEvent
from atlas.core.usage import Conformity, UsageEntry, UsageLog
from atlas.llm.base import LLMConfigError
from atlas.llm.factory import build_client
from atlas.logging_setup import configure_logging, dotenv_secrets
from atlas.skills.base import SkillContext
from atlas.skills.loader import SkillRegistry, load_skills
from atlas.storage.database import IN_MEMORY, StorageError, connect
from atlas.storage.migrator import MigrationError, apply_migrations, core_source
from atlas.storage.receipts import SqliteReceiptStore
from atlas.storage.trace import SqliteTraceStore
from atlas.storage.usage import SqliteUsageStore

logger = logging.getLogger(__name__)

app = typer.Typer(help="ATLAS : agent IA personnel, local-first.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Force Typer à garder les sous-commandes, même lorsqu'il n'y en a qu'une."""


@app.command()
def version() -> None:
    """Affiche la version d'ATLAS."""
    typer.echo(__version__)


def _skills_table(registry: SkillRegistry) -> Table:
    """Construit le tableau des compétences, avec une ligne par outil."""
    table = Table(title="Compétences")
    for column in ("Compétence", "Version", "Outil", "Effet"):
        table.add_column(column)
    for loaded in registry.skills:
        manifest = loaded.manifest
        if not loaded.tools:
            table.add_row(manifest.name, manifest.version, "aucun", "-")
        for spec in loaded.tools:
            effect = spec.effect or "lecture seule"
            table.add_row(manifest.name, manifest.version, spec.name, effect)
    return table


def _errors_table(registry: SkillRegistry) -> Table:
    """Construit le tableau des erreurs ; Text évite d'interpréter les crochets des messages."""
    table = Table(title="Erreurs de chargement", title_style="bold red")
    table.add_column("Dossier")
    table.add_column("Erreur", overflow="fold")
    for error in registry.errors:
        table.add_row(Text(error.folder), Text(error.message))
    return table


def _load_settings(console: Console) -> Settings:
    """Lit les réglages et installe les journaux, ou quitte si un réglage est invalide."""
    try:
        settings = Settings()
    except ValidationError as error:
        console.print(Text(f"Configuration invalide : {error}", style="bold red"))
        raise typer.Exit(code=1) from error
    secrets = dotenv_secrets(Path(".env"))
    if settings.openrouter_api_key is not None:
        secrets.append(settings.openrouter_api_key.get_secret_value())
    configure_logging(settings.log_level, settings.resolved_log_dir, secrets)
    return settings


@app.command()
def skills() -> None:
    """Liste les compétences, leurs outils, leurs effets et les erreurs de chargement."""
    console = Console()
    settings = _load_settings(console)
    # Base en mémoire : lister les compétences ne doit ni créer ni modifier de fichier.
    connection = connect(IN_MEMORY)
    try:
        registry = load_skills(context=SkillContext(connection, settings))
        try:
            apply_migrations(connection, [core_source(), *registry.migration_sources()])
        except MigrationError as error:
            console.print(Text(f"Migrations invalides : {error}", style="bold red"))
        console.print(_skills_table(registry))
        if registry.errors:
            console.print(_errors_table(registry))
    finally:
        connection.close()


VERDICT_STYLES = {
    Verdict.VERIFIED: ("vérifié", "green"),
    Verdict.CORRECTED: ("corrigé", "yellow"),
    Verdict.BLOCKED: ("bloqué", "bold red"),
    Verdict.NO_CLAIMS: ("sans affirmation d'action", "dim"),
    Verdict.ERROR: ("erreur", "bold red"),
}
STATUS_STYLES = {
    ReceiptStatus.OK: "green",
    ReceiptStatus.ERROR: "red",
    ReceiptStatus.PENDING_APPROVAL: "yellow",
    ReceiptStatus.REJECTED: "red",
}
YES_ANSWERS = {"o", "oui", "y", "yes"}
NO_ANSWERS = {"n", "non", "no"}
EXIT_WORDS = {"exit", "quit"}


def _dollars(amount: float | None) -> str:
    return "n/a" if amount is None else f"${amount:.4f}"


ARGUMENTS_WIDTH = 60
# Sous cet effectif, un taux de conformité ne dit presque rien : le rapport l'indique.
MIN_SAMPLE = 10


def _short(value: Any) -> str:
    """Valeur d'argument sur une ligne, sans retour à la ligne."""
    shown = value if isinstance(value, str) else json.dumps(value, ensure_ascii=False, default=str)
    return " ".join(shown.split())


def summarize_arguments(args: Any, width: int = ARGUMENTS_WIDTH) -> str:
    """Résume les arguments d'un reçu en une ligne « clé=valeur », tronquée à width."""
    if args is None or args == {}:
        return "(aucun)"
    if isinstance(args, dict):
        summary = ", ".join(f"{key}={_short(value)}" for key, value in args.items())
    else:
        summary = _short(args)
    return summary if len(summary) <= width else summary[: width - 1] + "…"


def actions_panel(receipts: list[Receipt]) -> RenderableType | None:
    """Encart « Actions de ce tour », construit uniquement depuis les reçus du journal.

    Il ne dépend ni du message du modèle ni de ce que celui-ci a cité : c'est ce qui a vraiment
    été tenté, avec l'issue. Une action soumise à validation y figure deux fois : le reçu en
    attente, puis la décision qui y est liée (« ← n° »).
    """
    if not receipts:
        return None
    table = Table(show_header=True, header_style="dim", box=None, padding=(0, 2))
    for column in ("Reçu", "Outil", "Arguments", "Statut"):
        table.add_column(column, overflow="fold")
    for receipt in receipts:
        link = f" ← n°{receipt.parent_id}" if receipt.parent_id else ""
        table.add_row(
            f"n°{receipt.id}{link}",
            receipt.tool,
            summarize_arguments(receipt.args),
            Text(receipt.status.value, style=STATUS_STYLES[receipt.status]),
        )
    return Panel(table, title="Actions de ce tour", title_align="left", border_style="dim")


def render_turn(result: TurnResult, usage: UsageLog) -> RenderableType:
    """Met en forme un tour : message, encart des actions (reçus), avertissements, verdict."""
    # Ligne vide systématique entre le message et le reste.
    parts: list[RenderableType] = [Markdown(result.reply), Text()]
    panel = actions_panel(result.receipts)
    if panel is not None:
        parts.append(panel)
    for warning in result.warnings:
        parts.append(Text(f"⚠ {warning}", style="yellow"))
    if result.verdict is Verdict.BLOCKED:
        parts.append(
            Text(
                "⚠ Le message affirmait une action qu'aucun reçu ne prouve : il a été remplacé.",
                style="bold red",
            )
        )
    label, style = VERDICT_STYLES[result.verdict]
    parts.append(Text.assemble("Verdict : ", (label, style)))
    parts.append(
        Text(
            f"Tour : {result.tokens} tokens, {_dollars(result.cost)} · "
            f"Aujourd'hui : {usage.tokens_today()} tokens, {_dollars(usage.cost_today())}",
            style="dim",
        )
    )
    return Group(*parts)


def make_approver(console: Console) -> ApprovalHandler:
    """Crée la fonction qui montre l'action en attente et demande l'accord de l'utilisateur.

    Dans le doute (entrée interrompue, fin de saisie), la réponse est non.
    """

    def approve(receipt: Receipt) -> bool:
        arguments = json.dumps(receipt.args, ensure_ascii=False, indent=2, default=str)
        details = Group(
            Text(f"Outil : {receipt.tool}"),
            Text(f"Effet : {receipt.effect}"),
            Text(f"Arguments : {arguments}"),
        )
        console.print(Panel(details, title="Approbation requise", border_style="yellow"))
        while True:
            try:
                answer = console.input("Approuver ? (o/n) ").strip().lower()
            except (EOFError, KeyboardInterrupt):
                console.print("Saisie interrompue : action refusée.", style="yellow")
                return False
            if answer in YES_ANSWERS:
                return True
            if answer in NO_ANSWERS:
                return False
            console.print("Réponds par o (oui) ou n (non).", style="dim")

    return approve


def _open_storage(settings: Settings, console: Console) -> sqlite3.Connection:
    """Ouvre la base, ou quitte avec un message clair."""
    try:
        return connect(settings.database_path)
    except StorageError as error:
        console.print(Text(f"Base inutilisable : {error}", style="bold red"))
        raise typer.Exit(code=1) from error


@app.command()
def chat() -> None:
    """Ouvre une conversation avec ATLAS ; « exit » pour quitter."""
    console = Console()
    settings = _load_settings(console)
    try:
        client = build_client(settings)
        detector = build_detector(
            settings.claim_detector,
            timeout=settings.claim_detector_timeout,
            max_timeouts=settings.claim_detector_max_timeouts,
        )
    except (LLMConfigError, UnknownDetectorError, DetectorUnavailable) as error:
        console.print(Text(f"Configuration invalide : {error}", style="bold red"))
        raise typer.Exit(code=1) from error
    connection = _open_storage(settings, console)
    try:
        registry = load_skills(context=SkillContext(connection, settings))
        try:
            apply_migrations(connection, [core_source(), *registry.migration_sources()])
        except MigrationError as error:
            console.print(Text(f"Migrations en échec : {error}", style="bold red"))
            raise typer.Exit(code=1) from error
        if registry.errors:
            console.print(_errors_table(registry))
        usage = UsageLog(
            settings.daily_token_budget,
            settings.daily_cost_budget,
            store=SqliteUsageStore(connection),
        )
        agent = Agent(
            client,
            registry,
            SqliteReceiptStore(connection),
            usage,
            max_tool_rounds=settings.max_tool_rounds,
            history_messages=settings.history_messages,
            address_form=settings.address_form,
            approver=make_approver(console),
            detector=detector,
            trace=SqliteTraceStore(connection),
        )
        logger.info(
            "atlas chat démarré : fournisseur=%s détecteur=%s niveau=%s",
            settings.provider,
            settings.claim_detector,
            settings.log_level,
        )
        _conversation(console, agent, usage)
    finally:
        connection.close()


def _rate(count: int, total: int) -> str:
    """Taux avec ses effectifs ; sans cas, aucun chiffre n'est inventé.

    Un effectif sous MIN_SAMPLE est suivi d'un astérisque, expliqué sous le tableau.
    """
    figure = "non mesurable" if total == 0 else f"{count / total:.1%}"
    text = f"{figure} ({count}/{total})"
    return f"{text}*" if total < MIN_SAMPLE else text


def stats_table(today: Conformity, total: Conformity) -> Table:
    """Tableau du taux de conformité : part des tours terminés par l'outil respond."""
    table = Table(title="Conformité de l'outil respond", title_justify="left")
    for column in ("", "Aujourd'hui", "Total"):
        table.add_column(column, justify="left" if not column else "right")
    rows = (
        ("Terminés par respond", "respond"),
        ("Texte libre (non conformes)", "free_text"),
        ("Sans réponse du modèle", "error"),
    )
    for label, field in rows:
        table.add_row(label, str(getattr(today, field)), str(getattr(total, field)))
    table.add_row(
        "Taux de conformité",
        _rate(today.respond, today.answered),
        _rate(total.respond, total.answered),
    )
    return table


@contextmanager
def _core_database(console: Console, settings: Settings) -> Iterator[sqlite3.Connection | None]:
    """Ouvre la base existante et applique les migrations du cœur ; None si elle n'existe pas.

    Les commandes de lecture (stats, tour) ne chargent aucune compétence : les journaux du cœur
    leur suffisent. Elles ne créent jamais une base absente.
    """
    if not settings.database_path.exists():
        console.print(
            f"Aucune base à {settings.database_path} : lance « atlas chat » d'abord.", style="dim"
        )
        yield None
        return
    connection = _open_storage(settings, console)
    try:
        try:
            apply_migrations(connection, [core_source()])
        except MigrationError as error:
            console.print(Text(f"Migrations en échec : {error}", style="bold red"))
            raise typer.Exit(code=1) from error
        yield connection
    finally:
        connection.close()


@app.command()
def stats() -> None:
    """Affiche le taux de conformité : part des tours terminés par l'outil respond."""
    console = Console()
    settings = _load_settings(console)
    with _core_database(console, settings) as connection:
        if connection is None:
            return
        usage = UsageLog(
            settings.daily_token_budget,
            settings.daily_cost_budget,
            store=SqliteUsageStore(connection),
        )
        console.print(stats_table(usage.conformity(date.today()), usage.conformity()))
        console.print(
            f"* effectif insuffisant : moins de {MIN_SAMPLE} tours où le modèle a répondu.",
            style="dim",
        )
        console.print(
            f"Consommation du jour : {usage.tokens_today()} tokens, {_dollars(usage.cost_today())}",
            style="dim",
        )


REASON_LABELS = {
    Reason.NON_CONFORMING.value: "réponse non conforme (texte libre au lieu de l'outil respond)",
    Reason.INVALID_RECEIPT_NUMBERS.value: "numéro de reçu invalide",
    Reason.UNPROVED_CLAIMS.value: "affirmation détectée sans reçu qui la prouve",
    Reason.INVALID_RESPOND_ARGUMENTS.value: "appel respond inutilisable",
    Reason.RESPOND_WITH_OTHER_TOOLS.value: "respond appelé avec d'autres outils",
    Reason.EMPTY_RESPONSE.value: "réponse vide ou limite atteinte",
}
ACTION_LABELS = {
    Action.RETRY.value: "correction renvoyée au modèle",
    Action.REMOVED.value: "numéros retirés, réponse gardée",
    Action.BLOCKED.value: "message remplacé par un refus",
    Action.FALLBACK.value: "gardée, contrôlée par le détecteur de texte seul",
    Action.IGNORED.value: "appel ignoré",
    Action.FAILED.value: "le tour s'arrête",
}


def _refusal_details(data: dict[str, Any]) -> list[str]:
    """Détails d'un refus, une ligne chacun : numéros, affirmations détectées, message d'erreur."""
    lines: list[str] = []
    for item in data.get("numbers", []):
        lines.append(f"n°{item['number']} {item['why']}")
    claims = data.get("claims")
    if claims:
        detector = data.get("detector", "rules")
        cited = data.get("cited")
        proof = (
            "reçus cités : " + (", ".join(f"n°{n}" for n in cited) if cited else "aucun")
            if cited is not None
            else "aucun reçu cité (texte libre : tous les reçus du tour comptent)"
        )
        for claim in claims:
            effect = claim.get("effect") or "une action d'écriture"
            if claim.get("any_of"):
                effect = " ou ".join(claim["any_of"])
            if not claim.get("declared", True):
                effect += " (aucun outil ne la déclare)"
            lines.append(
                f"détecteur « {detector} » : « {claim.get('text', '')} » "
                f"(effet attendu : {effect}) ; {proof}"
            )
    if data.get("detail") and not lines:
        lines.append(str(data["detail"]))
    return lines


def _usage_totals(entries: list[UsageEntry]) -> tuple[int, float | None]:
    costs = [entry.cost for entry in entries if entry.cost is not None]
    return sum(entry.tokens for entry in entries), (sum(costs) if costs else None)


def render_tour(
    number: int,
    turn_id: str,
    events: list[TraceEvent],
    receipts: list[Receipt],
    usage: list[UsageEntry],
) -> RenderableType:
    """Raconte un tour d'après la base : appels au modèle, outils, refus et leur raison, issue."""
    by_id = {receipt.id: receipt for receipt in receipts}
    start = next((event for event in events if event.kind is EventKind.START), None)
    when = "" if start is None else f" · {start.created_at.astimezone():%Y-%m-%d %H:%M:%S}"
    parts: list[RenderableType] = [Text(f"Tour n°{number} · {turn_id[:8]}{when}", style="bold")]
    call_number = 0
    final: TraceEvent | None = None
    for event in events:
        data = event.data
        if event.kind is EventKind.MODEL_CALL:
            call_number += 1
            tools = data.get("tool_calls") or []
            says = (
                "demande : " + ", ".join(tools)
                if tools
                else ("répond en texte libre" if data.get("has_text") else "ne renvoie rien")
            )
            estimated = " (estimé)" if data.get("estimated") else ""
            parts.append(
                Text(
                    f"Appel {call_number} au modèle ({data.get('model') or 'inconnu'}) : "
                    f"{data.get('tokens', 0)} tokens, {_dollars(data.get('cost'))}{estimated}"
                    f" · {says}"
                )
            )
        elif event.kind is EventKind.RECEIPT:
            receipt = by_id.get(data.get("id", 0))
            status = ReceiptStatus(data["status"])
            args = summarize_arguments(receipt.args) if receipt else "(reçu introuvable)"
            link = f" ← n°{receipt.parent_id}" if receipt and receipt.parent_id else ""
            parts.append(
                Text.assemble(
                    f"    reçu n°{data['id']}{link} {data['tool']} {args} → ",
                    (status.value, STATUS_STYLES[status]),
                )
            )
        elif event.kind is EventKind.REFUSAL:
            reason = REASON_LABELS.get(data.get("reason", ""), str(data.get("reason")))
            action = ACTION_LABELS.get(data.get("action", ""), str(data.get("action")))
            parts.append(Text(f"    ✗ Réponse non acceptée : {reason} → {action}", style="yellow"))
            for line in _refusal_details(data):
                parts.append(Text(f"        {line}", style="dim"))
        elif event.kind is EventKind.FINAL:
            final = event
    if call_number == 0:
        parts.append(Text("Aucun appel au modèle n'a été tracé.", style="dim"))
    if final is None:
        parts.append(Text("Issue du tour : non enregistrée (tour interrompu ?).", style="red"))
    else:
        verdict = Verdict(final.data["verdict"])
        label, style = VERDICT_STYLES[verdict]
        ended = {
            "respond": "terminé par respond (conforme)",
            "free_text": "terminé en texte libre (non conforme)",
            "error": "sans réponse du modèle",
        }.get(str(final.data.get("outcome")), "issue inconnue")
        parts.append(Text.assemble("Verdict final : ", (label, style), f" · {ended}"))
        cited = final.data.get("actions") or []
        if cited:
            parts.append(Text("Reçus cités par le modèle : " + ", ".join(f"n°{n}" for n in cited)))
        for warning in final.data.get("warnings") or []:
            parts.append(Text(f"⚠ {warning}", style="yellow"))
    tokens, cost = _usage_totals(usage)
    parts.append(Text(f"Total : {tokens} tokens, {_dollars(cost)}", style="dim"))
    return Group(*parts)


@app.command()
def tour(
    number: Annotated[
        int | None, typer.Argument(min=1, help="Numéro du tour (1 = le plus ancien).")
    ] = None,
    dernier: Annotated[bool, typer.Option("--dernier", help="Affiche le dernier tour.")] = False,
) -> None:
    """Raconte un tour : appels au modèle, outils et reçus, réponses refusées et pourquoi."""
    console = Console()
    if (number is None) == (not dernier):
        console.print(Text("Indique un numéro de tour, ou --dernier.", style="bold red"))
        raise typer.Exit(code=1)
    settings = _load_settings(console)
    with _core_database(console, settings) as connection:
        if connection is None:
            return
        trace = SqliteTraceStore(connection)
        total = trace.count_turns()
        turn_id = trace.last_turn_id() if dernier else trace.turn_id_at(number or 0)
        if turn_id is None:
            known = "aucun tour tracé" if total == 0 else f"{total} tour(s) tracé(s)"
            wanted = "Aucun tour" if dernier else f"Pas de tour n°{number}"
            console.print(
                Text(f"{wanted} dans la trace ({known}). Les tours antérieurs ne sont pas tracés."),
                style="red",
            )
            raise typer.Exit(code=1)
        position = trace.number_of(turn_id) or 0
        console.print(
            render_tour(
                position,
                turn_id,
                trace.events(turn_id),
                SqliteReceiptStore(connection).for_turn(turn_id),
                SqliteUsageStore(connection).for_turn(turn_id),
            )
        )


def _conversation(console: Console, agent: Agent, usage: UsageLog) -> None:
    """Boucle de saisie : une ligne, un tour, jusqu'à « exit »."""
    console.print("ATLAS est prêt. Tape « exit » pour quitter.", style="dim")
    while True:
        try:
            line = console.input("[bold cyan]Toi[/] > ").strip()
        except (EOFError, KeyboardInterrupt):
            break
        if line.lower() in EXIT_WORDS:
            break
        if not line:
            continue
        console.print(render_turn(agent.run_turn(line), usage))
