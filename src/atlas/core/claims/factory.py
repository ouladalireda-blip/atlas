"""Choix du détecteur d'affirmations par la configuration (ATLAS_CLAIM_DETECTOR)."""

from collections.abc import Callable

from atlas.core.claims.base import ClaimDetector
from atlas.core.claims.fallback import DEFAULT_MAX_TIMEOUTS, DEFAULT_TIMEOUT, FallbackDetector
from atlas.core.claims.rules import RulesDetector

DEFAULT_DETECTOR = "rules"

# Un constructeur par détecteur. Les candidats à dépendances lourdes importent leurs bibliothèques
# dans leur constructeur et lèvent DetectorUnavailable en nommant la commande à lancer.
_BUILDERS: dict[str, Callable[[], ClaimDetector]] = {
    DEFAULT_DETECTOR: RulesDetector,
}


class UnknownDetectorError(ValueError):
    """Le détecteur demandé n'existe pas."""


def detector_names() -> tuple[str, ...]:
    """Retourne les noms de détecteurs que la configuration accepte."""
    return tuple(_BUILDERS)


def default_detector() -> ClaimDetector:
    """Retourne le détecteur de référence (R), utilisé quand aucun autre n'est fourni."""
    return RulesDetector()


def build_detector(
    name: str = DEFAULT_DETECTOR,
    *,
    resilient: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    max_timeouts: int = DEFAULT_MAX_TIMEOUTS,
) -> ClaimDetector:
    """Construit le détecteur nommé ; refuse un nom inconnu avec la liste des noms valides.

    Avec resilient (le cas de l'exécution), un détecteur autre que R est enveloppé : s'il tombe
    en panne ou dépasse timeout secondes par appel, les règles prennent le relais et un
    avertissement est journalisé ; après max_timeouts dépassements, il est désactivé pour la
    session. Les mesures
    demandent resilient=False pour voir les pannes au lieu de les masquer. Si le détecteur ne peut
    pas démarrer, DetectorUnavailable remonte : le démarrage est refusé.
    """
    builder = _BUILDERS.get(name)
    if builder is None:
        known = ", ".join(_BUILDERS)
        raise UnknownDetectorError(
            f"Détecteur d'affirmations inconnu : {name!r} "
            f"(ATLAS_CLAIM_DETECTOR ; valeurs acceptées : {known})."
        )
    detector = builder()
    if not resilient or name == DEFAULT_DETECTOR:
        return detector
    return FallbackDetector(detector, default_detector(), timeout, max_timeouts)
