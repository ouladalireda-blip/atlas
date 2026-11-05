"""Tests du délai maximal d'un détecteur : dépassement = repli sur les règles + avertissement."""

import logging
import threading
import time

import pytest
from pydantic import ValidationError

from atlas.config import Settings
from atlas.core.claims import (
    DEFAULT_MAX_TIMEOUTS,
    DEFAULT_TIMEOUT,
    Claim,
    Domains,
    FallbackDetector,
    RulesDetector,
    build_detector,
    factory,
)
from atlas.core.claims.rules import detect_claims
from atlas.core.lexicon import Domain

DOMAINS: Domains = {"email": Domain(nouns=("mail",), actions={"send": "email.send"})}


class Slow:
    """Détecteur factice lent : attend qu'on le libère (ou 5 s au plus) avant de répondre."""

    name = "lent"

    def __init__(self) -> None:
        self.release = threading.Event()
        self.started = threading.Event()
        self.calls = 0

    def detect(self, reply: str, domains: Domains) -> list[Claim]:
        self.calls += 1
        self.started.set()
        self.release.wait(5)
        return [Claim("email.send", "réponse tardive")]


class Quick:
    name = "rapide"

    def detect(self, reply: str, domains: Domains) -> list[Claim]:
        return [Claim("email.send", "x")]


class Broken:
    name = "cassé"

    def detect(self, reply: str, domains: Domains) -> list[Claim]:
        raise RuntimeError("panne")


def test_default_timeout_is_one_second(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_CLAIM_DETECTOR_TIMEOUT", raising=False)

    assert DEFAULT_TIMEOUT == 1.0
    assert Settings(_env_file=None).claim_detector_timeout == 1.0  # type: ignore[call-arg]


def test_a_slow_detector_falls_back_on_the_rules_with_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    slow = Slow()
    detector = FallbackDetector(slow, RulesDetector(), timeout=0.05)

    with caplog.at_level(logging.WARNING, logger="atlas.core.claims.fallback"):
        started = time.perf_counter()
        claims = detector.detect("Le mail est parti.", DOMAINS)
        elapsed = time.perf_counter() - started
    slow.release.set()

    assert slow.started.is_set()
    assert elapsed < 2  # on n'a pas attendu les 5 s du détecteur lent
    assert claims == detect_claims("Le mail est parti.", DOMAINS)
    assert detector.timeouts == 1
    assert detector.failures == 0
    warnings = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    assert "lent" in warnings[0]
    assert "délai" in warnings[0]
    assert "rules" in warnings[0]


def test_the_late_answer_of_a_slow_detector_is_ignored() -> None:
    slow = Slow()
    detector = FallbackDetector(slow, RulesDetector(), timeout=0.05)

    first = detector.detect("Bonjour", DOMAINS)
    slow.release.set()  # les appels suivants répondent tout de suite
    second = detector.detect("Bonjour", DOMAINS)

    assert first == []
    assert [claim.text for claim in second] == ["réponse tardive"]


def test_a_detector_within_the_timeout_is_used_without_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    detector = FallbackDetector(Quick(), RulesDetector(), timeout=5)

    with caplog.at_level(logging.WARNING):
        claims = detector.detect("Bonjour", DOMAINS)

    assert claims == [Claim("email.send", "x")]
    assert detector.timeouts == 0
    assert caplog.records == []


def test_a_failure_is_still_a_failure_not_a_timeout(caplog: pytest.LogCaptureFixture) -> None:
    detector = FallbackDetector(Broken(), RulesDetector(), timeout=5)

    with caplog.at_level(logging.WARNING):
        detector.detect("Le mail est parti.", DOMAINS)

    assert (detector.failures, detector.timeouts) == (1, 0)
    assert "RuntimeError" in caplog.records[0].getMessage()


@pytest.mark.parametrize("timeout", [0, -1, -0.5])
def test_a_non_positive_timeout_is_refused(timeout: float) -> None:
    with pytest.raises(ValueError, match="délai"):
        FallbackDetector(Broken(), RulesDetector(), timeout=timeout)


def test_the_factory_passes_the_timeout_to_the_fallback(monkeypatch: pytest.MonkeyPatch) -> None:
    slow = Slow()
    monkeypatch.setitem(factory._BUILDERS, "lent", lambda: slow)

    detector = build_detector("lent", timeout=0.05)
    detector.detect("Bonjour", DOMAINS)
    slow.release.set()

    assert isinstance(detector, FallbackDetector)
    assert detector.timeouts == 1


def test_settings_timeout_must_be_positive(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_CLAIM_DETECTOR_TIMEOUT", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]

    monkeypatch.setenv("ATLAS_CLAIM_DETECTOR_TIMEOUT", "2.5")
    assert Settings(_env_file=None).claim_detector_timeout == 2.5  # type: ignore[call-arg]


class Interrupting:
    name = "interrompu"

    def detect(self, reply: str, domains: Domains) -> list[Claim]:
        raise KeyboardInterrupt


def test_an_interruption_is_not_swallowed_by_the_fallback() -> None:
    """Seules les exceptions ordinaires déclenchent le repli ; un arrêt demandé remonte."""
    detector = FallbackDetector(Interrupting(), RulesDetector(), timeout=5)

    with pytest.raises(KeyboardInterrupt):
        detector.detect("Bonjour", DOMAINS)
    assert detector.failures == 0


# --- disjoncteur -------------------------------------------------------------------------------


def test_default_circuit_breaker_tolerates_three_timeouts(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_CLAIM_DETECTOR_MAX_TIMEOUTS", raising=False)

    assert DEFAULT_MAX_TIMEOUTS == 3
    assert Settings(_env_file=None).claim_detector_max_timeouts == 3  # type: ignore[call-arg]


def test_after_three_timeouts_the_detector_is_disabled_for_the_session(
    caplog: pytest.LogCaptureFixture,
) -> None:
    slow = Slow()
    detector = FallbackDetector(slow, RulesDetector(), timeout=0.02)
    expected = detect_claims("Le mail est parti.", DOMAINS)

    with caplog.at_level(logging.WARNING, logger="atlas.core.claims.fallback"):
        results = [detector.detect("Le mail est parti.", DOMAINS) for _ in range(3)]
        assert detector.disabled is True
        calls_when_tripped = slow.calls
        later = [detector.detect("Le mail est parti.", DOMAINS) for _ in range(5)]
    slow.release.set()

    assert results == [expected] * 3
    assert later == [expected] * 5
    assert slow.calls == calls_when_tripped == 3  # plus aucun appel, donc plus de fil bloqué
    assert detector.timeouts == 3
    messages = [r.getMessage() for r in caplog.records if r.levelno == logging.WARNING]
    disabling = [m for m in messages if "désactivé pour la session" in m]
    assert len(disabling) == 1
    assert "lent" in disabling[0] and "3" in disabling[0] and "rules" in disabling[0]
    assert len(messages) == 4  # 3 dépassements + 1 désactivation, rien pour les appels suivants


def test_the_detector_stays_enabled_below_the_limit() -> None:
    slow = Slow()
    detector = FallbackDetector(slow, RulesDetector(), timeout=0.02, max_timeouts=3)

    detector.detect("a", DOMAINS)
    detector.detect("b", DOMAINS)

    assert detector.disabled is False
    assert slow.calls == 2


def test_a_disabled_detector_does_not_come_back_even_when_it_recovers() -> None:
    slow = Slow()
    detector = FallbackDetector(slow, RulesDetector(), timeout=0.02, max_timeouts=1)

    detector.detect("a", DOMAINS)
    slow.release.set()  # le détecteur se rétablit, mais le disjoncteur reste ouvert
    result = detector.detect("Le mail est parti.", DOMAINS)

    assert detector.disabled is True
    assert result == detect_claims("Le mail est parti.", DOMAINS)
    assert slow.calls == 1


def test_the_limit_is_adjustable() -> None:
    slow = Slow()
    detector = FallbackDetector(slow, RulesDetector(), timeout=0.02, max_timeouts=1)

    detector.detect("a", DOMAINS)

    assert detector.disabled is True


def test_ordinary_failures_do_not_trip_the_breaker(caplog: pytest.LogCaptureFixture) -> None:
    detector = FallbackDetector(Broken(), RulesDetector(), timeout=5, max_timeouts=1)

    with caplog.at_level(logging.WARNING):
        for _ in range(5):
            detector.detect("Bonjour", DOMAINS)

    assert detector.disabled is False
    assert detector.failures == 5


@pytest.mark.parametrize("limit", [0, -1])
def test_a_limit_below_one_is_refused(limit: int) -> None:
    with pytest.raises(ValueError, match="au moins 1"):
        FallbackDetector(Broken(), RulesDetector(), max_timeouts=limit)


def test_the_factory_passes_the_limit(monkeypatch: pytest.MonkeyPatch) -> None:
    slow = Slow()
    monkeypatch.setitem(factory._BUILDERS, "lent", lambda: slow)

    detector = build_detector("lent", timeout=0.02, max_timeouts=1)
    detector.detect("a", DOMAINS)
    slow.release.set()

    assert isinstance(detector, FallbackDetector)
    assert detector.disabled is True


def test_settings_limit_must_be_at_least_one(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_CLAIM_DETECTOR_MAX_TIMEOUTS", "0")
    with pytest.raises(ValidationError):
        Settings(_env_file=None)  # type: ignore[call-arg]

    monkeypatch.setenv("ATLAS_CLAIM_DETECTOR_MAX_TIMEOUTS", "5")
    assert Settings(_env_file=None).claim_detector_max_timeouts == 5  # type: ignore[call-arg]
