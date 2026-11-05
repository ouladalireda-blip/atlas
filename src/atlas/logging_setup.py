"""Journalisation d'ATLAS : stderr et fichier tournant, avec masquage des secrets.

Seuls les journaux de l'espace de noms `atlas` sont capturés : les bibliothèques tierces (httpx,
httpcore...) restent muettes, car leurs traces de débogage peuvent contenir des en-têtes
d'authentification.

- Le fichier (`<log_dir>/atlas.log`, rotation simple) reçoit tout ce qui atteint le niveau choisi.
- Le terminal (stderr) reçoit les avertissements et pires ; avec le niveau DEBUG, il reçoit tout.
- Une valeur secrète connue (clé API, valeurs sensibles du fichier .env) ou une forme de clé
  courante est remplacée par `***` dans chaque ligne, traces d'exceptions comprises.
"""

import logging
import re
import sys
from collections.abc import Iterable
from io import TextIOWrapper
from logging.handlers import RotatingFileHandler
from pathlib import Path

ROOT_LOGGER = "atlas"
LOG_FILENAME = "atlas.log"
MAX_BYTES = 1_000_000
BACKUP_COUNT = 3
LEVELS = ("DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL")
REDACTED = "***"
FORMAT = "%(asctime)s %(levelname)s %(name)s: %(message)s"

# Les variables d'environnement dont le nom ressemble à ceci contiennent un secret.
_SECRET_NAME = re.compile(r"KEY|TOKEN|SECRET|PASSWORD|PASSWD|CREDENTIAL", re.IGNORECASE)
# Sous cette longueur, une valeur serait remplacée partout dans le texte (« 1 », « ok »...).
MIN_SECRET_LENGTH = 6
# Formes courantes, même si la valeur exacte est inconnue : clés `sk-...`, en-têtes d'autorisation.
_KEY_SHAPES = re.compile(
    r"\bsk-[A-Za-z0-9_\-]{12,}"
    r"|(?i:\bbearer)\s+[A-Za-z0-9._~+/\-]{8,}=*"
    r"|(?i:authorization)\s*[:=]\s*\S+(?:\s+\S+)?"
)
_HANDLER_MARK = "_atlas_handler"


def normalize_level(value: str) -> str:
    """Retourne le niveau en majuscules, ou lève ValueError avec les niveaux acceptés."""
    level = value.strip().upper()
    if level not in LEVELS:
        raise ValueError(
            f"niveau de journal inconnu {value!r} (valeurs acceptées : {', '.join(LEVELS)})"
        )
    return level


def dotenv_secrets(path: Path = Path(".env")) -> list[str]:
    """Lit les valeurs sensibles d'un fichier .env (noms contenant KEY, TOKEN, SECRET...).

    Un fichier absent ou illisible donne une liste vide : le masquage par forme reste actif.
    """
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return []
    secrets: list[str] = []
    for line in lines:
        name, separator, value = line.partition("=")
        name = name.strip().removeprefix("export ").strip()
        value = value.strip().strip("'\"")
        if separator and not name.startswith("#") and _SECRET_NAME.search(name):
            secrets.append(value)
    return secrets


class RedactingFormatter(logging.Formatter):
    """Formateur qui masque les secrets dans la ligne finale, exception comprise."""

    def __init__(self, fmt: str, secrets: Iterable[str] = ()) -> None:
        super().__init__(fmt)
        # Les plus longues d'abord : un secret contenu dans un autre ne doit pas le casser.
        usable = {secret for secret in secrets if len(secret) >= MIN_SECRET_LENGTH}
        self._secrets = sorted(usable, key=len, reverse=True)

    def redact(self, text: str) -> str:
        """Remplace chaque secret connu et chaque forme de clé par `***`."""
        for secret in self._secrets:
            text = text.replace(secret, REDACTED)
        return _KEY_SHAPES.sub(REDACTED, text)

    def format(self, record: logging.LogRecord) -> str:
        """Met en forme l'enregistrement puis masque le résultat."""
        return self.redact(super().format(record))


class _LazyFileHandler(RotatingFileHandler):
    """Fichier tournant créé à la première ligne écrite, pas à la configuration.

    Une commande qui ne journalise rien (atlas skills, atlas version) ne crée donc ni dossier ni
    fichier. Si le fichier est inutilisable (dossier protégé, disque plein...), un avertissement
    s'affiche une fois sur stderr et les journaux continuent sur le terminal : la conversation
    n'est jamais bloquée par la journalisation.
    """

    def __init__(self, path: Path) -> None:
        super().__init__(
            path, maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8", delay=True
        )
        self._warned = False

    def _open(self) -> TextIOWrapper:
        Path(self.baseFilename).parent.mkdir(parents=True, exist_ok=True)
        return super()._open()

    def handleError(self, record: logging.LogRecord) -> None:
        """Remplace la trace d'erreur de logging par une ligne claire, une seule fois."""
        if not self._warned:
            self._warned = True
            print(
                f"Fichier de journal inutilisable ({self.baseFilename}) : "
                "journaux sur stderr seulement.",
                file=sys.stderr,
            )


def reset_logging() -> None:
    """Retire les gestionnaires installés par configure_logging (tests, reconfiguration)."""
    logger = logging.getLogger(ROOT_LOGGER)
    for handler in list(logger.handlers):
        if getattr(handler, _HANDLER_MARK, False):
            logger.removeHandler(handler)
            handler.close()


def configure_logging(level: str, log_dir: Path, secrets: Iterable[str] = ()) -> Path:
    """Installe les journaux ; retourne le chemin du fichier (créé à la première ligne écrite).

    Idempotent : une seconde configuration remplace la première.
    """
    normalized = normalize_level(level)
    reset_logging()
    logger = logging.getLogger(ROOT_LOGGER)
    logger.setLevel(normalized)
    formatter = RedactingFormatter(FORMAT, list(secrets))

    console = logging.StreamHandler(sys.stderr)
    console.setLevel(logging.DEBUG if normalized == "DEBUG" else logging.WARNING)
    console.setFormatter(formatter)
    setattr(console, _HANDLER_MARK, True)
    logger.addHandler(console)

    path = log_dir / LOG_FILENAME
    file_handler = _LazyFileHandler(path)
    file_handler.setLevel(normalized)
    file_handler.setFormatter(formatter)
    setattr(file_handler, _HANDLER_MARK, True)
    logger.addHandler(file_handler)
    return path
