"""Migrations numérotées : des fichiers .sql appliqués dans l'ordre, chacun dans une transaction.

Chaque source (le cœur, ou une compétence) a ses propres numéros : la table schema_migrations
est indexée par (source, version). Une source est un dossier de fichiers NNNN_nom.sql, numérotés
à partir de 1 sans trou.
"""

import hashlib
import re
import sqlite3
from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

CORE_SOURCE = "core"
CORE_MIGRATIONS_DIR = Path(__file__).parent / "migrations"
_FILENAME = re.compile(r"^(\d{4})_([a-z][a-z0-9_]*)\.sql$")
_COMMENTS = re.compile(r"--[^\n]*|/\*.*?\*/", re.DOTALL)
# La transaction appartient au système : une migration ne doit pas la fermer ni en ouvrir une.
_FORBIDDEN_FIRST_WORDS = frozenset({"begin", "commit", "end", "rollback", "savepoint", "release"})


class MigrationError(Exception):
    """Une migration est invalide ou a échoué ; la base reste dans son dernier état cohérent."""


@dataclass(frozen=True)
class MigrationSource:
    """Dossier de migrations d'un propriétaire : « core » ou le nom d'une compétence."""

    name: str
    directory: Path


@dataclass(frozen=True)
class Migration:
    """Un fichier de migration lu et contrôlé."""

    source: str
    version: int
    name: str
    statements: tuple[str, ...]
    checksum: str

    @property
    def label(self) -> str:
        return f"{self.source} {self.version:04d}_{self.name}"


def core_source() -> MigrationSource:
    """Retourne la source des migrations du cœur."""
    return MigrationSource(CORE_SOURCE, CORE_MIGRATIONS_DIR)


def split_statements(sql: str) -> list[str]:
    """Découpe un script en instructions, sans executescript (qui validerait la transaction)."""
    statements: list[str] = []
    start = 0
    for position, character in enumerate(sql):
        # Un point-virgule ne termine l'instruction que s'il n'est ni dans une chaîne, ni dans
        # un commentaire, ni dans le corps d'un trigger : sqlite3 en juge sur tout le tampon.
        if character == ";" and sqlite3.complete_statement(sql[start : position + 1]):
            statements.append(sql[start : position + 1].strip())
            start = position + 1
    rest = sql[start:]
    if _COMMENTS.sub("", rest).strip():
        raise MigrationError(f"Instruction non terminée par un point-virgule : {rest.strip()!r}")
    return statements


def _read(source: MigrationSource, version: int, name: str, path: Path) -> Migration:
    try:
        sql = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as error:
        raise MigrationError(f"Migration illisible ({path}) : {error}") from error
    statements = split_statements(sql)
    if not statements:
        raise MigrationError(f"Migration vide : {path}")
    for statement in statements:
        first = re.match(r"\s*(\w+)", _COMMENTS.sub("", statement))
        if first and first.group(1).lower() in _FORBIDDEN_FIRST_WORDS:
            raise MigrationError(
                f"{path.name} contient {first.group(1).upper()} : "
                "la transaction est gérée par ATLAS."
            )
    checksum = hashlib.sha256(sql.encode("utf-8")).hexdigest()
    return Migration(source.name, version, name, tuple(statements), checksum)


def discover(source: MigrationSource) -> list[Migration]:
    """Lit et contrôle les migrations d'une source : noms valides, numérotation sans trou."""
    if not source.directory.is_dir():
        raise MigrationError(
            f"Dossier de migrations introuvable ({source.name}) : {source.directory}"
        )
    found: dict[int, Migration] = {}
    for path in sorted(source.directory.iterdir()):
        if path.name.startswith(".") or path.is_dir() or path.suffix == ".pyc":
            continue
        match = _FILENAME.match(path.name)
        if match is None:
            raise MigrationError(
                f"Nom de migration invalide ({source.name}) : {path.name} (attendu NNNN_nom.sql)."
            )
        version = int(match.group(1))
        if version in found:
            raise MigrationError(f"Numéro de migration en double ({source.name}) : {version:04d}.")
        found[version] = _read(source, version, match.group(2), path)
    expected = list(range(1, len(found) + 1))
    if sorted(found) != expected:
        raise MigrationError(
            f"Numérotation invalide ({source.name}) : {sorted(found)} (attendu {expected})."
        )
    return [found[version] for version in expected]


def _applied(connection: sqlite3.Connection, source: str) -> dict[int, tuple[str, str]]:
    rows = connection.execute(
        "SELECT version, name, checksum FROM schema_migrations WHERE source = ?", (source,)
    ).fetchall()
    return {row["version"]: (row["name"], row["checksum"]) for row in rows}


def _check_history(
    source: str, migrations: list[Migration], applied: dict[int, tuple[str, str]]
) -> None:
    """Refuse une base incohérente avec les fichiers : plus récente, ou migration modifiée."""
    if sorted(applied) != list(range(1, len(applied) + 1)):
        raise MigrationError(
            f"Historique de migrations incohérent ({source}) : versions {sorted(applied)}."
        )
    known = {migration.version: migration for migration in migrations}
    for version, (name, checksum) in sorted(applied.items()):
        migration = known.get(version)
        if migration is None:
            raise MigrationError(
                f"La base contient la migration {source} {version:04d}_{name}, inconnue de ce "
                "code : elle vient d'une version plus récente d'ATLAS."
            )
        if migration.checksum != checksum:
            raise MigrationError(
                f"La migration {migration.label} a été modifiée après son application. "
                "Ne modifie jamais une migration appliquée : ajoute-en une nouvelle."
            )


def _apply_one(connection: sqlite3.Connection, migration: Migration) -> None:
    """Applique une migration et note sa version dans la même transaction."""
    try:
        connection.execute("BEGIN IMMEDIATE")
        for statement in migration.statements:
            connection.execute(statement)
        connection.execute(
            "INSERT INTO schema_migrations (source, version, name, checksum, applied_at) "
            "VALUES (?, ?, ?, ?, ?)",
            (
                migration.source,
                migration.version,
                migration.name,
                migration.checksum,
                datetime.now(UTC).isoformat(timespec="seconds"),
            ),
        )
        connection.execute("COMMIT")
    except Exception as error:
        if connection.in_transaction:
            connection.execute("ROLLBACK")
        raise MigrationError(
            f"Migration {migration.label} échouée et annulée (rollback) : {error}"
        ) from error


def apply_migrations(
    connection: sqlite3.Connection, sources: Sequence[MigrationSource]
) -> list[str]:
    """Applique les migrations en attente de chaque source ; retourne les libellés appliqués.

    Une migration appliquée n'est jamais rejouée. Chaque migration a sa transaction : si la
    troisième échoue, les deux premières restent appliquées et l'erreur nomme la troisième.
    """
    names = [source.name for source in sources]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise MigrationError(f"Source de migrations en double : {', '.join(duplicates)}.")
    plans = [(source, discover(source)) for source in sources]
    try:
        connection.execute(
            "CREATE TABLE IF NOT EXISTS schema_migrations ("
            "source TEXT NOT NULL, version INTEGER NOT NULL, name TEXT NOT NULL, "
            "checksum TEXT NOT NULL, applied_at TEXT NOT NULL, PRIMARY KEY (source, version))"
        )
        applied_now: list[str] = []
        for source, migrations in plans:
            applied = _applied(connection, source.name)
            _check_history(source.name, migrations, applied)
            for migration in migrations:
                if migration.version not in applied:
                    _apply_one(connection, migration)
                    applied_now.append(migration.label)
    except sqlite3.Error as error:
        raise MigrationError(f"Base inutilisable pendant les migrations : {error}") from error
    return applied_now
