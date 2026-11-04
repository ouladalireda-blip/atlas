"""Connexion SQLite : fichier configurable, dossier créé si absent."""

import sqlite3
from pathlib import Path

DEFAULT_DATABASE = Path("data/atlas.db")
IN_MEMORY = ":memory:"
# Délai d'attente d'un verrou, en secondes : au-delà, l'erreur est explicite plutôt qu'un blocage.
LOCK_TIMEOUT = 5.0


class StorageError(Exception):
    """La base n'a pas pu être ouverte ; le message est destiné à l'utilisateur."""


def connect(path: Path | str = DEFAULT_DATABASE) -> sqlite3.Connection:
    """Ouvre la base, en créant le dossier parent si besoin.

    La connexion est en mode autocommit : les transactions sont démarrées explicitement
    (voir migrations.py), jamais ouvertes implicitement par le module sqlite3.
    """
    target = str(path)
    in_memory = target == IN_MEMORY
    try:
        if not in_memory:
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(target, isolation_level=None, timeout=LOCK_TIMEOUT)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        if not in_memory:
            connection.execute("PRAGMA journal_mode = WAL")
    except (OSError, sqlite3.Error) as error:
        raise StorageError(f"Impossible d'ouvrir la base {target} : {error}") from error
    return connection
