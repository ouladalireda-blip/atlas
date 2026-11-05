"""Tests de la journalisation : stderr et fichier, niveaux, rotation, et surtout aucun secret."""

import logging
import os
from pathlib import Path

import pytest
from typer.testing import CliRunner

from atlas import logging_setup
from atlas.config import Settings
from atlas.interfaces import cli
from atlas.llm.scripted import ScriptedLLM
from atlas.logging_setup import (
    LOG_FILENAME,
    REDACTED,
    RedactingFormatter,
    configure_logging,
    dotenv_secrets,
    normalize_level,
    reset_logging,
)

runner = CliRunner()
API_KEY = "sk-or-v1-0123456789abcdef0123456789abcdef"
DOTENV_TOKEN = "jeton-prive-de-test-4242"
DOTENV_PASSWORD = "motdepasse-secret-9"


@pytest.fixture
def log_dir(tmp_path: Path) -> Path:
    return tmp_path / "logs"


def emit(name: str = "atlas.test") -> logging.Logger:
    return logging.getLogger(name)


def file_text(log_dir: Path) -> str:
    return (log_dir / LOG_FILENAME).read_text(encoding="utf-8")


# --- niveaux et destinations -------------------------------------------------------------------


def test_debug_goes_to_the_terminal_and_to_the_file(
    log_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("DEBUG", log_dir)

    emit().debug("détail technique")
    emit().info("information")

    err = capsys.readouterr().err
    assert "détail technique" in err and "information" in err
    text = file_text(log_dir)
    assert "DEBUG atlas.test: détail technique" in text
    assert "INFO atlas.test: information" in text


def test_info_level_writes_the_file_but_shows_only_warnings_on_the_terminal(
    log_dir: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    configure_logging("INFO", log_dir)

    emit().debug("trop fin")
    emit().info("dans le fichier seulement")
    emit().warning("visible partout")

    err = capsys.readouterr().err
    assert "dans le fichier seulement" not in err and "trop fin" not in err
    assert "visible partout" in err
    text = file_text(log_dir)
    assert "trop fin" not in text
    assert "dans le fichier seulement" in text and "visible partout" in text


def test_warning_level_excludes_info_from_the_file(log_dir: Path) -> None:
    configure_logging("warning", log_dir)

    emit().info("ignoré")
    emit().error("gardé")

    text = file_text(log_dir)
    assert "ignoré" not in text and "gardé" in text


def test_the_file_is_created_at_the_first_line_not_at_configuration(log_dir: Path) -> None:
    path = configure_logging("INFO", log_dir)

    assert not log_dir.exists()
    emit().info("première ligne")
    assert path == log_dir / LOG_FILENAME and path.exists()


def test_reconfiguring_does_not_stack_handlers(log_dir: Path) -> None:
    configure_logging("INFO", log_dir)
    configure_logging("INFO", log_dir)
    configure_logging("DEBUG", log_dir)

    emit().info("une seule fois")

    assert file_text(log_dir).count("une seule fois") == 1
    assert len(logging.getLogger("atlas").handlers) == 2


def test_reset_removes_our_handlers_only(log_dir: Path) -> None:
    other = logging.NullHandler()
    logging.getLogger("atlas").addHandler(other)
    configure_logging("INFO", log_dir)

    reset_logging()

    assert logging.getLogger("atlas").handlers == [other]
    logging.getLogger("atlas").removeHandler(other)


def test_third_party_libraries_are_not_captured(log_dir: Path) -> None:
    configure_logging("DEBUG", log_dir)

    logging.getLogger("httpx").debug("Authorization: Bearer abcdefgh12345")
    emit().debug("à nous")

    text = file_text(log_dir)
    assert "à nous" in text and "httpx" not in text and "abcdefgh12345" not in text


def test_the_file_rotates_with_a_few_backups(
    log_dir: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(logging_setup, "MAX_BYTES", 300)
    configure_logging("INFO", log_dir)

    for number in range(60):
        emit().info("ligne de remplissage numéro %d", number)

    names = sorted(path.name for path in log_dir.iterdir())
    assert LOG_FILENAME in names and f"{LOG_FILENAME}.1" in names
    assert len(names) <= 1 + logging_setup.BACKUP_COUNT


def test_an_unusable_log_file_never_blocks_and_warns_once(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    blocked = tmp_path / "pas-un-dossier"
    blocked.write_text("je suis un fichier", encoding="utf-8")
    configure_logging("INFO", blocked)

    emit().warning("premier")
    emit().warning("second")

    err = capsys.readouterr().err
    assert err.count("Fichier de journal inutilisable") == 1
    assert "premier" in err and "second" in err


def test_unknown_level_is_refused_with_the_accepted_values() -> None:
    with pytest.raises(ValueError, match="DEBUG, INFO, WARNING, ERROR, CRITICAL"):
        normalize_level("bavard")
    assert normalize_level(" debug ") == "DEBUG"


# --- réglages ------------------------------------------------------------------------------------


def test_settings_normalise_the_level_and_refuse_an_unknown_one(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("ATLAS_LOG_LEVEL", "debug")
    assert Settings(_env_file=None).log_level == "DEBUG"  # type: ignore[call-arg]

    monkeypatch.setenv("ATLAS_LOG_LEVEL", "bavard")
    with pytest.raises(ValueError, match="bavard"):
        Settings(_env_file=None)  # type: ignore[call-arg]


def test_log_directory_defaults_next_to_the_database(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_LOG_DIR", raising=False)
    monkeypatch.delenv("ATLAS_DATABASE_PATH", raising=False)
    assert Settings(_env_file=None).resolved_log_dir == Path("data/logs")  # type: ignore[call-arg]

    monkeypatch.setenv("ATLAS_DATABASE_PATH", "ailleurs/x.db")
    assert Settings(_env_file=None).resolved_log_dir == Path("ailleurs/logs")  # type: ignore[call-arg]

    monkeypatch.setenv("ATLAS_LOG_DIR", "mes-journaux")
    assert Settings(_env_file=None).resolved_log_dir == Path("mes-journaux")  # type: ignore[call-arg]


# --- masquage des secrets ---------------------------------------------------------------------


def format_with(secrets: list[str], message: str, *args: object) -> str:
    formatter = RedactingFormatter("%(message)s", secrets)
    record = logging.LogRecord("atlas.t", logging.INFO, __file__, 1, message, args, None)
    return formatter.format(record)


def test_known_secrets_are_masked_in_messages_and_arguments() -> None:
    line = format_with([API_KEY], "clé %s puis %s", API_KEY, f"préfixe-{API_KEY}-suffixe")

    assert API_KEY not in line
    assert line.count(REDACTED) == 2


def test_key_shapes_are_masked_even_when_the_value_is_unknown() -> None:
    other = "sk-proj-AAAAAAAAAAAAAAAAAAAAAAAA"
    line = format_with(
        [],
        "%s | Authorization: Bearer abcdef0123456789 | Bearer zyxwvutsrqpo",
        other,
    )

    assert other not in line and "abcdef0123456789" not in line and "zyxwvutsrqpo" not in line


def test_secrets_in_a_traceback_are_masked() -> None:
    formatter = RedactingFormatter("%(message)s", [API_KEY])
    try:
        raise RuntimeError(f"échec avec {API_KEY}")
    except RuntimeError:
        import sys

        record = logging.LogRecord(
            "atlas.t", logging.ERROR, __file__, 1, "erreur", (), sys.exc_info()
        )

    line = formatter.format(record)

    assert "RuntimeError" in line and API_KEY not in line and REDACTED in line


def test_overlapping_secrets_do_not_leave_a_fragment() -> None:
    line = format_with(["abcdef", "abcdefghij"], "valeur abcdefghij")

    assert "abcdef" not in line and "ghij" not in line


def test_very_short_values_are_not_treated_as_secrets() -> None:
    assert format_with(["1", "ok", ""], "ok 1 est vrai") == "ok 1 est vrai"


def test_ordinary_text_is_untouched() -> None:
    text = "Tour a1b2c3d4 : verdict=verified outils=['agenda_add'] sk-court"
    assert format_with([API_KEY], text) == text


def test_dotenv_secrets_keep_only_sensitive_names(tmp_path: Path) -> None:
    env = tmp_path / ".env"
    env.write_text(
        "# commentaire\n"
        "ATLAS_LOG_LEVEL=DEBUG\n"
        f"ATLAS_OPENROUTER_API_KEY={API_KEY}\n"
        f"export MY_TOKEN='{DOTENV_TOKEN}'\n"
        f'DB_PASSWORD="{DOTENV_PASSWORD}"\n'
        "# FAUX_SECRET=commente-123456\n"
        "SANS_VALEUR\n",
        encoding="utf-8",
    )

    assert dotenv_secrets(env) == [API_KEY, DOTENV_TOKEN, DOTENV_PASSWORD]


def test_missing_dotenv_gives_no_secret(tmp_path: Path) -> None:
    assert dotenv_secrets(tmp_path / "absent") == []


# --- bout en bout : atlas chat ---------------------------------------------------------------


def test_chat_logs_contain_neither_the_api_key_nor_the_dotenv_content(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Une panne qui cite la clé et le contenu du .env ne laisse rien dans les journaux."""
    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("ATLAS_"):
            monkeypatch.delenv(name)
    (tmp_path / ".env").write_text(
        f"ATLAS_LOG_LEVEL=DEBUG\nATLAS_OPENROUTER_API_KEY={API_KEY}\n"
        f"SERVICE_TOKEN={DOTENV_TOKEN}\nDB_PASSWORD={DOTENV_PASSWORD}\n",
        encoding="utf-8",
    )
    boom = RuntimeError(f"échec : clé={API_KEY} jeton={DOTENV_TOKEN} mdp={DOTENV_PASSWORD}")
    monkeypatch.setattr(cli, "build_client", lambda settings: ScriptedLLM([boom]))

    result = runner.invoke(cli.app, ["chat"], input="Bonjour\nexit\n")

    log = (tmp_path / "data" / "logs" / LOG_FILENAME).read_text(encoding="utf-8")
    assert "Erreur inattendue dans le tour" in log  # la panne a bien été journalisée
    assert "atlas chat démarré" in log and "niveau=DEBUG" in log
    everything = log + result.output
    for secret in (API_KEY, DOTENV_TOKEN, DOTENV_PASSWORD):
        assert secret not in everything
    assert REDACTED in log


def test_debug_logs_describe_the_turn_without_quoting_the_user_message(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    from atlas.llm.scripted import tool_calls

    monkeypatch.chdir(tmp_path)
    for name in list(os.environ):
        if name.startswith("ATLAS_"):
            monkeypatch.delenv(name)
    monkeypatch.setenv("ATLAS_LOG_LEVEL", "DEBUG")
    reply = tool_calls(("respond", {"message": "Réponse privée à Zorglub.", "actions": []}))
    monkeypatch.setattr(cli, "build_client", lambda settings: ScriptedLLM([reply]))

    result = runner.invoke(cli.app, ["chat"], input="Mon secret est Marcel Dupont\nexit\n")

    log = (tmp_path / "data" / "logs" / LOG_FILENAME).read_text(encoding="utf-8")
    assert "start" in log and "model_call" in log and "final" in log
    assert "verdict=no_claims" in log
    assert "Marcel" not in log and "Zorglub" not in log
    assert "model_call" in result.output  # DEBUG : aussi sur le terminal (stderr)
