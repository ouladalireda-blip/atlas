"""Tests du contrat des détecteurs d'affirmations, de la fabrique et du repli sur les règles."""

import logging
from pathlib import Path

import pytest
from pydantic import ValidationError

from atlas.config import Settings
from atlas.core.claims import (
    DEFAULT_DETECTOR,
    Claim,
    ClaimDetector,
    DetectorUnavailable,
    Domains,
    FallbackDetector,
    RulesDetector,
    UnknownDetectorError,
    build_detector,
    default_detector,
    detector_names,
    factory,
)
from atlas.core.claims.rules import detect_claims
from atlas.core.lexicon import Domain
from atlas.core.receipts import Receipt, ReceiptStatus
from atlas.core.verifier import verify

DOMAINS: Domains = {
    "calendar": Domain(nouns=("événement", "agenda"), actions={"write": "calendar.write"}),
    "email": Domain(nouns=("mail",), actions={"send": "email.send"}),
}
REPLIES = [
    "J'ai ajouté l'événement à ton agenda.",
    "Le mail est parti.",
    "Je n'ai pas envoyé le mail.",
    "Tu as un événement demain.",
    "C'est envoyé !",
    "",
]


class Broken:
    """Détecteur qui tombe toujours en panne."""

    name = "cassé"

    def detect(self, reply: str, domains: Domains) -> list[Claim]:
        raise RuntimeError("modèle indisponible")


class Fixed:
    """Détecteur qui retourne toujours les mêmes affirmations."""

    def __init__(self, name: str, claims: list[Claim]) -> None:
        self.name = name
        self.claims = claims
        self.calls: list[str] = []

    def detect(self, reply: str, domains: Domains) -> list[Claim]:
        self.calls.append(reply)
        return self.claims


def ok(effect: str) -> Receipt:
    return Receipt(id=1, turn_id="t", tool="x", effect=effect, args={}, status=ReceiptStatus.OK)


def test_rules_detector_gives_exactly_the_results_of_the_rules() -> None:
    detector = RulesDetector()

    for reply in REPLIES:
        assert detector.detect(reply, DOMAINS) == detect_claims(reply, DOMAINS)
    assert detector.name == "rules"


def test_every_registered_detector_follows_the_contract() -> None:
    for name in detector_names():
        detector: ClaimDetector = build_detector(name, resilient=False)
        assert detector.name == name
        assert isinstance(detector.detect("Le mail est parti.", DOMAINS), list)


def test_the_default_is_the_rules_and_the_names_are_listed() -> None:
    assert DEFAULT_DETECTOR == "rules"
    assert "rules" in detector_names()
    assert build_detector().name == "rules"
    assert isinstance(default_detector(), RulesDetector)


def test_unknown_detector_is_refused_with_the_valid_names() -> None:
    with pytest.raises(UnknownDetectorError) as error:
        build_detector("magie")

    message = str(error.value)
    assert "magie" in message
    assert "ATLAS_CLAIM_DETECTOR" in message
    assert "rules" in message


@pytest.mark.parametrize("name", ["", " rules", "RULES", "spacy "])
def test_detector_names_are_matched_exactly(name: str) -> None:
    with pytest.raises(UnknownDetectorError):
        build_detector(name)


def test_a_detector_that_cannot_start_refuses_the_startup(monkeypatch: pytest.MonkeyPatch) -> None:
    def missing() -> ClaimDetector:
        raise DetectorUnavailable("extra manquant : lancer « uv sync --extra nlp »")

    monkeypatch.setitem(factory._BUILDERS, "lourd", missing)

    with pytest.raises(DetectorUnavailable, match="uv sync --extra nlp"):
        build_detector("lourd")


def test_a_registered_candidate_is_wrapped_for_the_fallback_unless_measuring(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setitem(factory._BUILDERS, "lourd", Broken)

    wrapped = build_detector("lourd")
    raw = build_detector("lourd", resilient=False)

    assert isinstance(wrapped, FallbackDetector)
    assert wrapped.name == "cassé"
    assert isinstance(raw, Broken)
    # Pour une mesure, la panne doit se voir et non être masquée par le repli.
    with pytest.raises(RuntimeError):
        raw.detect("x", DOMAINS)


def test_rules_are_never_wrapped() -> None:
    assert isinstance(build_detector("rules"), RulesDetector)


def test_failure_falls_back_on_the_rules_and_logs_a_warning(
    caplog: pytest.LogCaptureFixture,
) -> None:
    detector = FallbackDetector(Broken(), RulesDetector())

    with caplog.at_level(logging.WARNING, logger="atlas.core.claims.fallback"):
        claims = detector.detect("Le mail est parti.", DOMAINS)

    assert claims == detect_claims("Le mail est parti.", DOMAINS)
    assert detector.failures == 1
    warnings = [r for r in caplog.records if r.levelno == logging.WARNING]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert "cassé" in message
    assert "RuntimeError" in message
    assert "modèle indisponible" in message
    assert "rules" in message


def test_every_failure_is_logged_and_the_next_call_tries_the_primary_again(
    caplog: pytest.LogCaptureFixture,
) -> None:
    detector = FallbackDetector(Broken(), RulesDetector())

    with caplog.at_level(logging.WARNING, logger="atlas.core.claims.fallback"):
        detector.detect("a", DOMAINS)
        detector.detect("b", DOMAINS)

    assert detector.failures == 2
    assert len(caplog.records) == 2


def test_a_healthy_primary_is_used_without_warning(caplog: pytest.LogCaptureFixture) -> None:
    primary = Fixed("sain", [Claim("email.send", "x")])
    detector = FallbackDetector(primary, RulesDetector())

    with caplog.at_level(logging.WARNING):
        claims = detector.detect("Bonjour", DOMAINS)

    assert claims == [Claim("email.send", "x")]
    assert primary.calls == ["Bonjour"]
    assert detector.failures == 0
    assert caplog.records == []


def test_a_failing_fallback_is_not_hidden() -> None:
    detector = FallbackDetector(Broken(), Broken())

    with pytest.raises(RuntimeError):
        detector.detect("x", DOMAINS)


def test_verify_uses_the_rules_by_default() -> None:
    reply = "Le mail est parti."

    default = verify(reply, [], DOMAINS)
    explicit = verify(reply, [], DOMAINS, RulesDetector())

    assert default == explicit
    assert [claim.effect for claim in default.unsupported] == ["email.send"]


def test_verify_uses_the_given_detector_and_still_requires_receipts() -> None:
    claim = Claim("calendar.write", "J'ai tout fait.")
    detector = Fixed("fixe", [claim])

    unproved = verify("Bonjour", [], DOMAINS, detector)
    proved = verify("Bonjour", [ok("calendar.write")], DOMAINS, detector)
    wrong = verify("Bonjour", [ok("email.send")], DOMAINS, detector)

    assert unproved.unsupported == [claim]
    assert proved.unsupported == []
    assert proved.supported_by[claim][0].effect == "calendar.write"
    assert wrong.unsupported == [claim]
    assert detector.calls == ["Bonjour"] * 3


def test_verify_with_a_detector_that_finds_nothing_has_no_claims() -> None:
    result = verify("Le mail est parti.", [], DOMAINS, Fixed("muet", []))

    assert result.claims == []
    assert result.unsupported == []


def test_settings_default_and_valid_choice(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("ATLAS_CLAIM_DETECTOR", raising=False)
    assert Settings(_env_file=None).claim_detector == "rules"  # type: ignore[call-arg]

    monkeypatch.setenv("ATLAS_CLAIM_DETECTOR", "rules")
    assert Settings(_env_file=None).claim_detector == "rules"  # type: ignore[call-arg]


def test_settings_refuse_an_unknown_detector(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ATLAS_CLAIM_DETECTOR", "magie")

    with pytest.raises(ValidationError) as error:
        Settings(_env_file=None)  # type: ignore[call-arg]

    assert "magie" in str(error.value)
    assert "rules" in str(error.value)


def test_core_claims_do_not_depend_on_any_skill() -> None:
    """Le cœur ne dépend d'aucune compétence (règle d'architecture de CLAUDE.md)."""
    import atlas.core.claims as package

    for path in Path(package.__file__ or "").parent.glob("*.py"):
        assert "atlas.skills" not in path.read_text(encoding="utf-8"), path.name
