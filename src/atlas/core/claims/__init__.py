"""Détecteurs d'affirmations d'action : un contrat, une implémentation par candidat."""

from atlas.core.claims.base import Claim, ClaimDetector, DetectorUnavailable, Domains
from atlas.core.claims.factory import (
    DEFAULT_DETECTOR,
    UnknownDetectorError,
    build_detector,
    default_detector,
    detector_names,
)
from atlas.core.claims.fallback import DEFAULT_MAX_TIMEOUTS, DEFAULT_TIMEOUT, FallbackDetector
from atlas.core.claims.rules import RulesDetector

__all__ = [
    "DEFAULT_DETECTOR",
    "DEFAULT_MAX_TIMEOUTS",
    "DEFAULT_TIMEOUT",
    "Claim",
    "ClaimDetector",
    "DetectorUnavailable",
    "Domains",
    "FallbackDetector",
    "RulesDetector",
    "UnknownDetectorError",
    "build_detector",
    "default_detector",
    "detector_names",
]
