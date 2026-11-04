"""Interface en ligne de commande d'ATLAS."""

import typer
from rich.console import Console
from rich.table import Table
from rich.text import Text

from atlas import __version__
from atlas.skills.loader import SkillRegistry, load_skills

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
    registry = load_skills()
    console = Console()
    console.print(_skills_table(registry))
    if registry.errors:
        console.print(_errors_table(registry))
