"""Tests de la connexion SQLite et du système de migrations."""

import sqlite3
from pathlib import Path

import pytest

from atlas.storage.database import IN_MEMORY, StorageError, connect
from atlas.storage.migrator import (
    CORE_MIGRATIONS_DIR,
    MigrationError,
    MigrationSource,
    apply_migrations,
    core_source,
    discover,
    split_statements,
)


def source(tmp_path: Path, files: dict[str, str], name: str = "demo") -> MigrationSource:
    folder = tmp_path / name
    folder.mkdir(exist_ok=True)
    for filename, sql in files.items():
        (folder / filename).write_text(sql, encoding="utf-8")
    return MigrationSource(name, folder)


def versions(connection: sqlite3.Connection, name: str) -> list[int]:
    rows = connection.execute(
        "SELECT version FROM schema_migrations WHERE source = ? ORDER BY version", (name,)
    )
    return [row["version"] for row in rows]


def tables(connection: sqlite3.Connection) -> set[str]:
    rows = connection.execute("SELECT name FROM sqlite_master WHERE type = 'table'")
    return {row["name"] for row in rows}


@pytest.fixture
def db() -> sqlite3.Connection:
    return connect(IN_MEMORY)


# --- connexion -----------------------------------------------------------------------------


def test_connect_creates_the_missing_folder(tmp_path: Path) -> None:
    path = tmp_path / "a" / "b" / "atlas.db"

    connection = connect(path)
    connection.execute("CREATE TABLE t (x INTEGER)")
    connection.close()

    assert path.is_file()


def test_connect_enables_foreign_keys_and_rows_by_name(db: sqlite3.Connection) -> None:
    assert db.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert db.execute("SELECT 1 AS one").fetchone()["one"] == 1


def test_connect_fails_clearly_when_the_path_is_a_folder(tmp_path: Path) -> None:
    with pytest.raises(StorageError, match="Impossible d'ouvrir la base"):
        connect(tmp_path)


def test_connect_fails_clearly_when_the_parent_is_a_file(tmp_path: Path) -> None:
    blocker = tmp_path / "fichier"
    blocker.write_text("x", encoding="utf-8")

    with pytest.raises(StorageError, match="Impossible d'ouvrir la base"):
        connect(blocker / "atlas.db")


# --- base neuve, reprise -------------------------------------------------------------------


def test_fresh_database_gets_the_core_schema(db: sqlite3.Connection) -> None:
    applied = apply_migrations(db, [core_source()])

    assert applied == ["core 0001_receipts_usage", "core 0002_turn_log", "core 0003_turn_events"]
    assert {"receipts", "usage_log", "turn_log", "turn_events", "schema_migrations"} <= tables(db)
    assert versions(db, "core") == [1, 2, 3]


def test_running_twice_applies_nothing_the_second_time(db: sqlite3.Connection) -> None:
    apply_migrations(db, [core_source()])

    assert apply_migrations(db, [core_source()]) == []
    assert versions(db, "core") == [1, 2, 3]


def test_resume_applies_only_the_new_migrations(db: sqlite3.Connection, tmp_path: Path) -> None:
    demo = source(tmp_path, {"0001_a.sql": "CREATE TABLE a (x INTEGER);"})
    apply_migrations(db, [demo])
    (demo.directory / "0002_b.sql").write_text("CREATE TABLE b (x INTEGER);", encoding="utf-8")

    applied = apply_migrations(db, [demo])

    assert applied == ["demo 0002_b"]
    assert versions(db, "demo") == [1, 2]
    assert {"a", "b"} <= tables(db)


def test_each_source_has_its_own_numbering(db: sqlite3.Connection, tmp_path: Path) -> None:
    first = source(tmp_path, {"0001_a.sql": "CREATE TABLE a (x INTEGER);"}, "first")
    second = source(tmp_path, {"0001_b.sql": "CREATE TABLE b (x INTEGER);"}, "second")

    apply_migrations(db, [core_source(), first, second])

    assert (versions(db, "core"), versions(db, "first"), versions(db, "second")) == (
        [1, 2, 3],
        [1],
        [1],
    )


def test_an_empty_source_is_fine(db: sqlite3.Connection, tmp_path: Path) -> None:
    assert apply_migrations(db, [source(tmp_path, {}, "vide")]) == []


def test_applied_version_is_stored_with_name_and_checksum(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    apply_migrations(db, [source(tmp_path, {"0001_a.sql": "CREATE TABLE a (x INTEGER);"})])

    row = db.execute("SELECT * FROM schema_migrations").fetchone()

    assert (row["source"], row["version"], row["name"]) == ("demo", 1, "a")
    assert len(row["checksum"]) == 64
    assert row["applied_at"]


# --- échec et rollback ---------------------------------------------------------------------


def test_failure_rolls_back_the_whole_migration(db: sqlite3.Connection, tmp_path: Path) -> None:
    demo = source(
        tmp_path,
        {
            "0001_ok.sql": "CREATE TABLE ok (x INTEGER);",
            "0002_bad.sql": (
                "CREATE TABLE half (x INTEGER);\n"
                "INSERT INTO half VALUES (1);\n"
                "INSERT INTO table_inexistante VALUES (1);\n"
            ),
        },
    )

    with pytest.raises(MigrationError, match=r"demo 0002_bad échouée et annulée \(rollback\)"):
        apply_migrations(db, [demo])

    assert "half" not in tables(db)  # rien de la migration 2 ne subsiste
    assert "ok" in tables(db)  # la migration 1, déjà validée, reste
    assert versions(db, "demo") == [1]
    assert not db.in_transaction


def test_error_message_names_the_cause(db: sqlite3.Connection, tmp_path: Path) -> None:
    demo = source(tmp_path, {"0001_bad.sql": "CREATE TABLE t (x INTEGER;"})

    with pytest.raises(MigrationError, match="syntax error"):
        apply_migrations(db, [demo])


def test_a_failed_migration_can_be_fixed_and_replayed(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    demo = source(tmp_path, {"0001_a.sql": "CREATE TABLE a (x INTEGER); BOUM;"})
    with pytest.raises(MigrationError):
        apply_migrations(db, [demo])

    (demo.directory / "0001_a.sql").write_text("CREATE TABLE a (x INTEGER);", encoding="utf-8")

    assert apply_migrations(db, [demo]) == ["demo 0001_a"]


def test_migration_cannot_control_the_transaction(db: sqlite3.Connection, tmp_path: Path) -> None:
    demo = source(tmp_path, {"0001_a.sql": "CREATE TABLE a (x INTEGER); COMMIT;"})

    with pytest.raises(MigrationError, match="COMMIT"):
        apply_migrations(db, [demo])

    assert "a" not in tables(db)


# --- cohérence de l'historique -------------------------------------------------------------


def test_modified_applied_migration_is_refused(db: sqlite3.Connection, tmp_path: Path) -> None:
    demo = source(tmp_path, {"0001_a.sql": "CREATE TABLE a (x INTEGER);"})
    apply_migrations(db, [demo])
    (demo.directory / "0001_a.sql").write_text("CREATE TABLE a (y TEXT);", encoding="utf-8")

    with pytest.raises(MigrationError, match="modifiée après son application"):
        apply_migrations(db, [demo])


def test_database_newer_than_the_code_is_refused(db: sqlite3.Connection, tmp_path: Path) -> None:
    demo = source(
        tmp_path,
        {"0001_a.sql": "CREATE TABLE a (x INTEGER);", "0002_b.sql": "CREATE TABLE b (x INTEGER);"},
    )
    apply_migrations(db, [demo])
    (demo.directory / "0002_b.sql").unlink()

    with pytest.raises(MigrationError, match="version plus récente"):
        apply_migrations(db, [demo])


def test_inconsistent_history_is_refused(db: sqlite3.Connection, tmp_path: Path) -> None:
    demo = source(tmp_path, {"0001_a.sql": "CREATE TABLE a (x INTEGER);"})
    apply_migrations(db, [demo])
    db.execute("UPDATE schema_migrations SET version = 3")

    with pytest.raises(MigrationError, match="Historique de migrations incohérent"):
        apply_migrations(db, [demo])


# --- fichiers invalides --------------------------------------------------------------------


@pytest.mark.parametrize(
    ("files", "message"),
    [
        ({"1_a.sql": "SELECT 1;"}, "Nom de migration invalide"),
        ({"0001_A.sql": "SELECT 1;"}, "Nom de migration invalide"),
        ({"0001_a.txt": "SELECT 1;"}, "Nom de migration invalide"),
        ({"0002_a.sql": "SELECT 1;"}, "Numérotation invalide"),
        ({"0001_a.sql": "SELECT 1;", "0003_c.sql": "SELECT 1;"}, "Numérotation invalide"),
        ({"0001_a.sql": "SELECT 1;", "0001_b.sql": "SELECT 1;"}, "en double"),
        ({"0001_a.sql": ""}, "Migration vide"),
        ({"0001_a.sql": "-- seulement un commentaire\n"}, "Migration vide"),
        ({"0001_a.sql": "SELECT 1"}, "point-virgule"),
    ],
)
def test_invalid_migration_files_are_refused(
    db: sqlite3.Connection, tmp_path: Path, files: dict[str, str], message: str
) -> None:
    with pytest.raises(MigrationError, match=message):
        apply_migrations(db, [source(tmp_path, files)])

    assert tables(db) == set()  # rien n'est appliqué, pas même la table de suivi


def test_missing_folder_and_duplicate_sources_are_refused(
    db: sqlite3.Connection, tmp_path: Path
) -> None:
    with pytest.raises(MigrationError, match="introuvable"):
        apply_migrations(db, [MigrationSource("x", tmp_path / "absent")])
    twin = source(tmp_path, {"0001_a.sql": "SELECT 1;"})
    with pytest.raises(MigrationError, match="en double"):
        apply_migrations(db, [twin, twin])


def test_an_unreadable_encoding_is_refused(db: sqlite3.Connection, tmp_path: Path) -> None:
    demo = source(tmp_path, {})
    (demo.directory / "0001_a.sql").write_bytes(b"\xff\xfe\x00 pas de l'utf-8 \x80")

    with pytest.raises(MigrationError, match="illisible"):
        apply_migrations(db, [demo])


def test_a_locked_database_gives_a_clear_error(tmp_path: Path) -> None:
    path = tmp_path / "locked.db"
    first = connect(path)
    first.execute("BEGIN IMMEDIATE")
    second = connect(path)
    second.execute("PRAGMA busy_timeout = 50")  # le test n'attend pas 5 secondes
    demo = source(tmp_path, {"0001_a.sql": "CREATE TABLE a (x INTEGER);"})

    with pytest.raises(MigrationError, match="locked"):
        apply_migrations(second, [demo])

    first.execute("ROLLBACK")


# --- découpage -----------------------------------------------------------------------------


def test_split_statements_handles_comments_semicolons_and_triggers() -> None:
    sql = (
        "-- en-tête ; avec un point-virgule\n"
        "CREATE TABLE t (x TEXT DEFAULT 'a;b');\n"
        "CREATE TRIGGER trg AFTER INSERT ON t BEGIN\n"
        "  UPDATE t SET x = 'v';\n"
        "  DELETE FROM t WHERE 0;\n"
        "END;\n"
        "/* fin */\n"
    )

    statements = split_statements(sql)

    assert len(statements) == 2
    assert statements[0].endswith("DEFAULT 'a;b');")
    assert statements[1].startswith("CREATE TRIGGER")


def test_split_statements_handles_several_statements_on_one_line() -> None:
    assert split_statements("CREATE TABLE a (x); CREATE TABLE b (y);") == [
        "CREATE TABLE a (x);",
        "CREATE TABLE b (y);",
    ]


def test_core_migrations_ship_with_the_package() -> None:
    assert CORE_MIGRATIONS_DIR.is_dir()
    assert [m.version for m in discover(core_source())] == [1, 2, 3]
