"""Exécution des outils : chaque demande produit un reçu, jamais une exception."""

import logging
from typing import Any

from atlas.core.receipts import Receipt, ReceiptStatus, ReceiptStore
from atlas.skills.base import ToolArgumentError, ToolSpec, call
from atlas.skills.loader import SkillRegistry

logger = logging.getLogger(__name__)


def _run(spec: ToolSpec, args: Any) -> tuple[ReceiptStatus, Any]:
    """Appelle l'outil ; toute panne devient un statut error, Ctrl+C reste possible."""
    if not isinstance(args, dict):
        return ReceiptStatus.ERROR, f"Arguments invalides (objet JSON attendu) : {args!r}"
    try:
        return ReceiptStatus.OK, call(spec, args)
    except ToolArgumentError as error:
        return ReceiptStatus.ERROR, str(error)
    # SystemExit est capté pour qu'un outil n'arrête pas ATLAS ; Ctrl+C reste possible.
    except (Exception, SystemExit) as error:
        return ReceiptStatus.ERROR, f"{type(error).__name__} : {error}"


class ToolExecutor:
    """Exécute les outils du registre, sauf ceux dont l'effet attend une validation humaine."""

    def __init__(self, registry: SkillRegistry, store: ReceiptStore) -> None:
        self._tools = {spec.name: spec for spec in registry.tools}
        self._store = store
        self._requires_approval = registry.requires_approval()

    def execute(self, turn_id: str, tool_name: str, args: Any) -> Receipt:
        """Traite une demande d'outil et retourne son reçu.

        args peut être illisible (texte brut quand le JSON du modèle est invalide) : le reçu
        est alors `error` et l'outil n'est pas appelé. Un effet soumis à validation donne un
        reçu `pending_approval` ; l'outil n'est pas appelé non plus.
        """
        spec = self._tools.get(tool_name)
        effect = spec.effect if spec else None
        if spec is None:
            status, result = ReceiptStatus.ERROR, f"Outil inconnu : {tool_name}"
        elif not isinstance(args, dict):
            status, result = _run(spec, args)
        elif effect in self._requires_approval:
            status, result = ReceiptStatus.PENDING_APPROVAL, None
        else:
            status, result = _run(spec, args)
        receipt = Receipt(
            turn_id=turn_id, tool=tool_name, effect=effect, args=args, status=status, result=result
        )
        return self._record(receipt)

    def approve(self, pending: Receipt) -> Receipt:
        """Exécute l'action d'un reçu `pending_approval` que l'humain a validée.

        Le nouveau reçu (ok ou error) est lié au premier par parent_id. Un reçu qui n'est pas en
        attente, ou dont l'outil a disparu, donne un reçu `error` : rien n'est exécuté.
        """
        spec = self._tools.get(pending.tool)
        if pending.status is not ReceiptStatus.PENDING_APPROVAL:
            status, result = ReceiptStatus.ERROR, f"Le reçu n°{pending.id} n'est pas en attente."
        elif spec is None:
            status, result = ReceiptStatus.ERROR, f"Outil inconnu : {pending.tool}"
        else:
            status, result = _run(spec, pending.args)
        return self._record(self._follow_up(pending, status, result))

    def reject(self, pending: Receipt) -> Receipt:
        """Enregistre le refus humain d'une action en attente : l'outil n'est pas appelé."""
        result = "Refusé par l'utilisateur : l'action n'a pas été exécutée."
        return self._record(self._follow_up(pending, ReceiptStatus.REJECTED, result))

    @staticmethod
    def _follow_up(pending: Receipt, status: ReceiptStatus, result: Any) -> Receipt:
        return Receipt(
            turn_id=pending.turn_id,
            tool=pending.tool,
            effect=pending.effect,
            args=pending.args,
            status=status,
            result=result,
            parent_id=pending.id or None,
        )

    def _record(self, receipt: Receipt) -> Receipt:
        """Enregistre le reçu ; sans preuve conservée, il ne peut pas rester au statut ok."""
        try:
            return self._store.add(receipt)
        except Exception as error:
            logger.exception("Reçu non enregistré pour l'outil %s", receipt.tool)
            message = f"Reçu non enregistré (statut {receipt.status.value}) : {error}"
            return receipt.model_copy(update={"status": ReceiptStatus.ERROR, "result": message})
