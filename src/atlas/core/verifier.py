"""Vérification des affirmations d'action d'une réponse à partir des reçus d'outils.

La détection des affirmations est confiée à un `ClaimDetector` (voir `core/claims/`) ; ce module
confronte ce qu'elle trouve aux reçus du tour. Seul un reçu réussi compte comme preuve.
"""

from collections.abc import Sequence
from dataclasses import dataclass

from atlas.core.claims.base import Claim, ClaimDetector, Domains
from atlas.core.claims.factory import default_detector
from atlas.core.receipts import Receipt, ReceiptStatus

__all__ = ["Claim", "Verification", "correction_message", "verify"]


@dataclass(frozen=True)
class Verification:
    """Bilan d'une réponse : affirmations trouvées, non prouvées et preuves par affirmation."""

    claims: list[Claim]
    unsupported: list[Claim]
    supported_by: dict[Claim, list[Receipt]]


def _proves(receipt: Receipt, claim: Claim) -> bool:
    """Indique si un reçu réussi prouve l'affirmation ; une lecture ne prouve aucune écriture."""
    if claim.effect is None:
        return receipt.effect is not None
    if claim.any_of:
        return receipt.effect in claim.any_of
    return receipt.effect == claim.effect


def verify(
    reply: str,
    receipts: Sequence[Receipt],
    domains: Domains,
    detector: ClaimDetector | None = None,
) -> Verification:
    """Confronte la réponse aux reçus du tour ; seul le statut ok compte comme preuve.

    Une affirmation non déclarée par son domaine n'est jamais prouvée, quels que soient les reçus.
    """
    claims = (detector or default_detector()).detect(reply, domains)
    successful = [receipt for receipt in receipts if receipt.status is ReceiptStatus.OK]
    unsupported: list[Claim] = []
    supported_by: dict[Claim, list[Receipt]] = {}
    for claim in claims:
        proofs = (
            [receipt for receipt in successful if _proves(receipt, claim)] if claim.declared else []
        )
        if proofs:
            supported_by[claim] = proofs
        else:
            unsupported.append(claim)
    return Verification(claims=claims, unsupported=unsupported, supported_by=supported_by)


def _describe(claim: Claim) -> str:
    if not claim.declared:
        return (
            f"- « {claim.text} » (action « {claim.effect} » : aucun outil ne la déclare, "
            "donc rien ne peut la prouver)"
        )
    if claim.any_of:
        return (
            f"- « {claim.text} » (action sans objet précisé : il faut un reçu réussi de "
            f"l'un de ces effets : {', '.join(claim.any_of)})"
        )
    return f"- « {claim.text} » (effet attendu : {claim.effect or 'une action d’écriture'})"


def correction_message(verification: Verification) -> str | None:
    """Rédige la consigne de correction pour le modèle ; None si tout est prouvé."""
    if not verification.unsupported:
        return None
    lines = "\n".join(_describe(claim) for claim in verification.unsupported)
    return (
        "Ta réponse affirme des actions qu'aucun reçu d'outil réussi ne prouve :\n"
        f"{lines}\n"
        "Appelle l'outil nécessaire, ou réécris ta réponse en disant ce qui a réellement été "
        "fait, ce qui a échoué et ce qui attend une validation."
    )
