"""Contrat des détecteurs d'affirmations et types que tous les candidats partagent.

Un détecteur lit la réponse du modèle et retourne les actions qu'elle affirme. Il ne connaît ni les
reçus ni les outils : confronter les affirmations aux preuves est le travail de `core/verifier.py`.
"""

from collections.abc import Mapping
from dataclasses import dataclass
from typing import Protocol

from atlas.core.lexicon import Domain

type Domains = Mapping[str, Domain]


@dataclass(frozen=True)
class Claim:
    """Affirmation d'action ; effect vaut None si n'importe quelle écriture la prouve.

    declared vaut False quand le domaine du nom ne déclare pas la classe du verbe : l'effet est
    alors « <domaine>.<classe> » et aucun reçu ne peut le prouver.

    any_of est rempli pour une affirmation générique de classe, sans objet nommé et plusieurs
    domaines possibles (« c'est ajouté ») : elle est prouvée par un reçu réussi de n'importe lequel
    de ces effets. Son effect vaut alors « *.<classe> ».
    """

    effect: str | None
    text: str
    declared: bool = True
    any_of: tuple[str, ...] = ()


class DetectorUnavailable(RuntimeError):
    """Un détecteur ne peut pas démarrer (dépendance ou modèle absent) : le démarrage est refusé."""


class ClaimDetector(Protocol):
    """Contrat commun : retourner les affirmations d'action d'une réponse."""

    name: str

    def detect(self, reply: str, domains: Domains) -> list[Claim]:
        """Retourne les affirmations d'action de la réponse, sans doublon.

        Les domaines disent quels noms et quelles classes de verbes chaque compétence déclare.
        """
        ...
