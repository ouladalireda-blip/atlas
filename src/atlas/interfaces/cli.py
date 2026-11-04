"""Interface en ligne de commande d'ATLAS."""

import json
import sqlite3

import typer
from rich.console import Console, Group, RenderableType
from rich.markdown import Markdown
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

from atlas import __version__
from atlas.config import Settings
from atlas.core.agent import Agent, ApprovalHandler, TurnResult, Verdict
from atlas.core.receipts import Receipt, ReceiptStatus
from atlas.core.usage import UsageLog
from atlas.llm.base import LLMConfigError
from atlas.llm.factory import build_client
from atlas.skills.base import SkillContext
from atlas.skills.loader import SkillRegistry, load_skills
from atlas.storage.database import IN_MEMORY, StorageError, connect
from atlas.storage.migrator import MigrationError, apply_migrations, core_source
from atlas.storage.receipts import SqliteReceiptStore
from atlas.storage.usage import SqliteUsageStore

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


@app.command()
def skills() -> None:
    """Liste les compétences, leurs outils, leurs effets et les erreurs de chargement."""
    console = Console()
    # Base en mémoire : lister les compétences ne doit ni créer ni modifier de fichier.
    connection = connect(IN_MEMORY)
    try:
        registry = load_skills(context=SkillContext(connection, Settings()))
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


def render_turn(result: TurnResult, usage: UsageLog) -> RenderableType:
    """Met en forme un tour : réponse, reçus (outil, statut), verdict, tokens et coût."""
    # Ligne vide systématique entre la réponse et le bloc reçus/verdict.
    parts: list[RenderableType] = [Markdown(result.reply), Text()]
    if result.receipts:
        table = Table(show_header=True, header_style="dim", box=None, padding=(0, 2))
        for column in ("Reçu", "Outil", "Statut"):
            table.add_column(column)
        for receipt in result.receipts:
            style = STATUS_STYLES[receipt.status]
            link = f" ← n°{receipt.parent_id}" if receipt.parent_id else ""
            table.add_row(
                f"n°{receipt.id}{link}", receipt.tool, Text(receipt.status.value, style=style)
            )
        parts.append(table)
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
    settings = Settings()
    try:
        client = build_client(settings)
    except LLMConfigError as error:
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
        )
        _conversation(console, agent, usage)
    finally:
        connection.close()


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
