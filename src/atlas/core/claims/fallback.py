"""Repli sur les règles quand un détecteur tombe en panne ou dépasse son délai."""

import logging
import threading

from atlas.core.claims.base import Claim, ClaimDetector, Domains

logger = logging.getLogger(__name__)

# Délai maximal d'un appel à un détecteur, en secondes (réglable : ATLAS_CLAIM_DETECTOR_TIMEOUT).
DEFAULT_TIMEOUT = 1.0
# Dépassements de délai tolérés pendant une session avant de désactiver le détecteur.
DEFAULT_MAX_TIMEOUTS = 3


class FallbackDetector:
    """Enveloppe un détecteur et passe à un autre (les règles) s'il échoue ou tarde trop.

    Sûr par défaut : une réponse n'est jamais laissée sans contrôle parce qu'un modèle a échoué
    ou se fait attendre. Chaque repli est journalisé en avertissement avec sa cause. L'appel au
    détecteur principal se fait dans un fil secondaire pour pouvoir cesser de l'attendre ; Python
    ne sait pas interrompre un fil, donc un détecteur bloqué continue en arrière-plan (fil démon,
    qui n'empêche pas ATLAS de s'arrêter) mais son résultat est ignoré.

    Disjoncteur : après `max_timeouts` dépassements de délai dans la session (cumulés, pas
    forcément consécutifs), le détecteur est désactivé : toutes les réponses suivantes passent par
    le repli, sans nouvel appel ni nouveau fil bloqué, et un avertissement le signale une fois. Il
    ne se réarme pas avant la prochaine session. Les exceptions ordinaires ne comptent pas : un
    détecteur qui échoue vite ne bloque rien.
    """

    def __init__(
        self,
        primary: ClaimDetector,
        fallback: ClaimDetector,
        timeout: float = DEFAULT_TIMEOUT,
        max_timeouts: int = DEFAULT_MAX_TIMEOUTS,
    ) -> None:
        if timeout <= 0:
            raise ValueError(f"Le délai doit être positif (reçu : {timeout}).")
        if max_timeouts < 1:
            raise ValueError(
                f"Il faut tolérer au moins 1 dépassement de délai (reçu : {max_timeouts})."
            )
        self._primary = primary
        self._fallback = fallback
        self._timeout = timeout
        self._max_timeouts = max_timeouts
        self.name = primary.name
        self.disabled = False
        self.failures = 0
        self.timeouts = 0

    def detect(self, reply: str, domains: Domains) -> list[Claim]:
        """Délègue au détecteur principal ; en cas de panne ou de délai dépassé, au repli."""
        if self.disabled:
            return self._fallback.detect(reply, domains)
        outcome: list[list[Claim] | BaseException] = []

        def work() -> None:
            try:
                outcome.append(self._primary.detect(reply, domains))
            except BaseException as error:
                outcome.append(error)

        worker = threading.Thread(target=work, name=f"claims-{self.name}", daemon=True)
        worker.start()
        worker.join(self._timeout)
        if worker.is_alive():
            self.timeouts += 1
            self._warn(f"délai de {self._timeout:g} s dépassé")
            if self.timeouts >= self._max_timeouts:
                self.disabled = True
                logger.warning(
                    "Détecteur d'affirmations « %s » désactivé pour la session après %d "
                    "dépassements de délai : repli permanent sur « %s ».",
                    self._primary.name,
                    self.timeouts,
                    self._fallback.name,
                )
            return self._fallback.detect(reply, domains)
        result = outcome[0]
        if isinstance(result, BaseException):
            if not isinstance(result, Exception):
                raise result
            self.failures += 1
            self._warn(f"{type(result).__name__} : {result}")
            return self._fallback.detect(reply, domains)
        return result

    def _warn(self, cause: str) -> None:
        logger.warning(
            "Détecteur d'affirmations « %s » en panne (%s) : repli sur « %s ».",
            self._primary.name,
            cause,
            self._fallback.name,
        )
