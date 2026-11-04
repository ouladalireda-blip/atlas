"""Interface en ligne de commande d'ATLAS."""

import typer

from atlas import __version__

app = typer.Typer(help="ATLAS : agent IA personnel, local-first.", no_args_is_help=True)


@app.callback()
def main() -> None:
    """Force Typer à garder les sous-commandes, même lorsqu'il n'y en a qu'une."""


@app.command()
def version() -> None:
    """Affiche la version d'ATLAS."""
    typer.echo(__version__)
