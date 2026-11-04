"""Exécution des outils : chaque demande produit un reçu, jamais une exception."""

import logging
from typing import Any

from atlas.core.receipts import Receipt, ReceiptStatus, ReceiptStore
from atlas.skills.base import ToolArgumentError, call
from atlas.skills.loader import SkillRegistry

logger = logging.getLogger(__name__)


class ToolExecutor:
    """Exécute les outils du registre, sauf ceux dont l'effet attend une validation humaine."""

    def __init__(self, registry: SkillRegistry, store: ReceiptStore) -> None:
        self._tools = {spec.name: spec for spec in registry.tools}
        self._store = store
        self._requires_approval = registry.requires_approval()

    def execute(self, turn_id: str, tool_name: str, args: Any) -> Receipt:
        """Traite une demande d'outil et retourne son reçu.

        args peut être illisible (texte brut quand le JSON du modèle est invalide) : le reçu
        est alors `error` et l'outil n'est pas appelé.
        """
        spec = self._tools.get(tool_name)
        effect = spec.effect if spec else None
        if spec is None:
            status, result = ReceiptStatus.ERROR, f"Outil inconnu : {tool_name}"
        elif not isinstance(args, dict):
            status, result = (
                ReceiptStatus.ERROR,
                f"Arguments invalides (objet JSON attendu) : {args!r}",
            )
        elif effect in self._requires_approval:
            status, result = ReceiptStatus.PENDING_APPROVAL, None
        else:
            try:
                status, result = ReceiptStatus.OK, call(spec, args)
            except ToolArgumentError as error:
                status, result = ReceiptStatus.ERROR, str(error)
            # SystemExit est capté pour qu'un outil n'arrête pas ATLAS ; Ctrl+C reste possible.
            except (Exception, SystemExit) as error:
                status, result = ReceiptStatus.ERROR, f"{type(error).__name__} : {error}"
        receipt = Receipt(
            turn_id=turn_id, tool=tool_name, effect=effect, args=args, status=status, result=result
        )
        return self._record(receipt)

    def _record(self, receipt: Receipt) -> Receipt:
        """Enregistre le reçu ; sans preuve conservée, il ne peut pas rester au statut ok."""
        try:
            return self._store.add(receipt)
        except Exception as error:
            logger.exception("Reçu non enregistré pour l'outil %s", receipt.tool)
            message = f"Reçu non enregistré (statut {receipt.status.value}) : {error}"
            return receipt.model_copy(update={"status": ReceiptStatus.ERROR, "result": message})
