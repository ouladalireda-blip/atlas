"""Tests du vérificateur ; le vocabulaire métier n'existe qu'ici, jamais dans src/."""

import pytest

from atlas.core.lexicon import Domain
from atlas.core.receipts import Receipt, ReceiptStatus
from atlas.core.verifier import Claim, correction_message, detect_claims, verify

DOMAINS = {
    "calendar": Domain(
        nouns=("événement", "agenda", "rendez-vous", "event", "calendar"),
        actions={"write": "calendar.write"},
    ),
    "email": Domain(nouns=("mail", "e-mail", "email"), actions={"send": "email.send"}),
}
ORIGINAL_BUG = "Confirmé, l'événement est ajouté à ton agenda."


def receipt(effect: str | None, status: ReceiptStatus = ReceiptStatus.OK, id: int = 1) -> Receipt:
    return Receipt(id=id, turn_id="t1", tool="x", effect=effect, args={}, status=status)


def effects(reply: str) -> list[str | None]:
    return [claim.effect for claim in detect_claims(reply, DOMAINS)]


def test_original_bug_without_receipt_is_unsupported() -> None:
    result = verify(ORIGINAL_BUG, [], DOMAINS)

    # « Confirmé » seul est une affirmation sans objet (repli), puis la proposition complète.
    assert result.unsupported == [
        Claim("calendar.write", "Confirmé"),
        Claim("calendar.write", "l'événement est ajouté à ton agenda."),
    ]
    assert result.supported_by == {}


def test_same_sentence_with_ok_receipt_is_supported() -> None:
    proof = receipt("calendar.write")

    result = verify(ORIGINAL_BUG, [proof], DOMAINS)

    assert result.unsupported == []
    assert result.supported_by == {claim: [proof] for claim in result.claims}
    assert len(result.claims) == 2


@pytest.mark.parametrize("status", [ReceiptStatus.ERROR, ReceiptStatus.PENDING_APPROVAL])
def test_receipt_without_ok_status_proves_nothing(status: ReceiptStatus) -> None:
    result = verify(ORIGINAL_BUG, [receipt("calendar.write", status)], DOMAINS)

    assert len(result.unsupported) == len(result.claims) == 2


def test_receipt_of_another_effect_proves_nothing() -> None:
    result = verify(ORIGINAL_BUG, [receipt("email.send")], DOMAINS)

    assert len(result.unsupported) == len(result.claims) == 2


def test_each_claim_needs_its_own_receipt() -> None:
    reply = "J'ai créé l'événement et j'ai envoyé le mail."

    result = verify(reply, [receipt("calendar.write")], DOMAINS)

    assert [claim.effect for claim in result.unsupported] == ["email.send"]


@pytest.mark.parametrize(
    "reply",
    [
        "L'événement n'a pas été ajouté à ton agenda.",
        "Je n'ai pas ajouté l'événement.",
        "Je n'ai jamais envoyé ce mail.",
        "Je n'ai ni créé l'événement ni envoyé le mail.",
        "Je n'ai pas créé l'événement et envoyé le mail.",
        "The event wasn't added to your calendar.",
        "The event was not added.",
    ],
)
def test_negation_is_not_a_claim(reply: str) -> None:
    assert detect_claims(reply, DOMAINS) == []


@pytest.mark.parametrize(
    "reply",
    [
        "Je vais ajouter l'événement à ton agenda.",
        "L'événement sera ajouté demain.",
        "L'événement va être ajouté.",
        "Je peux envoyer le mail.",
        "Si l'événement est ajouté, je te préviens.",
        "L'événement doit être validé.",
        "Il faudrait que l'événement soit ajouté.",
        "I'll add the event to your calendar.",
        "The event will be added.",
        "The email can be sent later.",
    ],
)
def test_future_or_possibility_is_not_a_claim(reply: str) -> None:
    assert detect_claims(reply, DOMAINS) == []


@pytest.mark.parametrize(
    "reply",
    [
        "L'événement est-il ajouté ?",
        "Veux-tu que l'événement soit ajouté à ton agenda ?",
        "Est-ce que le mail est envoyé, ou faut-il recommencer ?",
        "Was the event added?",
        "L'événement est ajouté ?!",
        "C'est fait ?",
    ],
)
def test_question_is_not_a_claim(reply: str) -> None:
    assert detect_claims(reply, DOMAINS) == []


@pytest.mark.parametrize(
    "reply",
    [
        "Voici un exemple :\n```\nl'événement est ajouté\n```",
        "Le texte `événement ajouté` est un libellé.",
        "```\nl'événement est ajouté",
    ],
)
def test_code_is_ignored(reply: str) -> None:
    assert detect_claims(reply, DOMAINS) == []


@pytest.mark.parametrize(
    "reply",
    [
        "J'ai consulté ton agenda.",
        "Ta journée contient un événement.",
        "La durée de l'événement est d'une heure.",
        "L'ajout de l'événement a échoué.",
        "The event failed.",
        "I checked your calendar.",
        "Désolé pour cet événement.",
    ],
)
def test_reading_failure_and_lookalike_words_are_not_claims(reply: str) -> None:
    assert detect_claims(reply, DOMAINS) == []


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("J'ai créé l'événement et envoyé le mail.", ["calendar.write", "email.send"]),
        ("Je n'ai pas créé l'événement mais j'ai envoyé le mail.", ["email.send"]),
        ("L'événement a été ajouté sans problème.", ["calendar.write"]),
        ("L'événement ajouté sera visible demain.", ["calendar.write"]),
        ("Les événements sont ajoutés.", ["calendar.write"]),
        ("Je t'ai envoyé le mail.", ["email.send"]),
        ("Ton rendez-vous est bien enregistré.", ["calendar.write"]),
        ("L’e-mail est envoyé.", ["email.send"]),
        ("L'agenda est mis à jour.", ["calendar.write"]),
        ("Je vais vérifier, mais l'événement est ajouté.", ["calendar.write"]),
        ("C'est ajouté à ton agenda, veux-tu un rappel ?", ["calendar.write"]),
        ("**Événement ajouté !**", ["calendar.write"]),
        ("L'événement est ajouté.", ["calendar.write"]),
        ("The event was added to your calendar.", ["calendar.write"]),
        ("I sent the email.", ["email.send"]),
        ("Le mail est parti.", ["email.send"]),
    ],
)
def test_claims_are_detected(reply: str, expected: list[str]) -> None:
    assert effects(reply) == expected


@pytest.mark.parametrize(
    "reply",
    [
        "Dès que tu valides, l'événement est ajouté.",
        "Dès qu'il valide, l'événement est ajouté.",
        "Une fois que tu valides, le mail est envoyé.",
        "Une fois qu'il valide, le mail est envoyé.",
        "Quand tu auras validé, l'événement est ajouté.",
        "Si tu confirmes, l'événement est ajouté et le mail est envoyé.",
        "Once approved, the event gets added.",
        "When approved, the event is added.",
        "If approved, the email is sent.",
    ],
)
def test_condition_blocks_the_rest_of_the_sentence(reply: str) -> None:
    assert detect_claims(reply, DOMAINS) == []


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("L'événement est ajouté, quand tu veux on continue.", ["calendar.write"]),
        ("Le mail est envoyé si besoin.", ["email.send"]),
        ("Si tu veux. L'événement est ajouté.", ["calendar.write"]),
        ("Une fois encore, l'événement est ajouté.", ["calendar.write"]),
    ],
)
def test_condition_does_not_block_what_comes_before_or_another_sentence(
    reply: str, expected: list[str]
) -> None:
    assert effects(reply) == expected


def test_a_distant_noun_does_not_count_so_the_class_fallback_applies() -> None:
    reply = "Tout est validé pour ce projet long et complexe que nous suivons dans ton agenda."

    # L'agenda est à plus de 6 mots : le verbe n'a pas d'objet à portée. Seul calendar déclare la
    # classe write, donc l'affirmation vaut calendar.write (repli, ADR 0005).
    assert effects(reply) == ["calendar.write"]


def test_same_claim_is_reported_once() -> None:
    assert effects("L'événement est ajouté et enregistré.") == ["calendar.write"]


def test_empty_noun_matches_nothing_so_only_the_fallback_remains() -> None:
    domain = Domain(nouns=("", "   "), actions={"write": "any.write"})

    claims = detect_claims("Tout est validé.", {"any": domain})

    assert [claim.effect for claim in claims] == ["any.write"]
    assert detect_claims("Tout est bien rangé.", {"any": domain}) == []


@pytest.mark.parametrize("reply", ["C'est fait !", "C'est bien fait.", "Done.", "All done!"])
def test_generic_done_needs_a_write_receipt(reply: str) -> None:
    assert verify(reply, [], {}).claims[0].effect is None
    assert len(verify(reply, [], {}).unsupported) == 1
    assert verify(reply, [receipt("any.write")], {}).unsupported == []


@pytest.mark.parametrize(
    "receipts",
    [[receipt(None)], [receipt("any.write", ReceiptStatus.ERROR)]],
    ids=["read_only", "failed_write"],
)
def test_generic_done_refuses_reads_and_failures(receipts: list[Receipt]) -> None:
    assert len(verify("C'est fait.", receipts, {}).unsupported) == 1


@pytest.mark.parametrize(
    "reply", ["Ce n'est pas fait.", "It is not done.", "Si c'est fait, dis-le."]
)
def test_generic_rule_respects_negation_and_hypothesis(reply: str) -> None:
    assert detect_claims(reply, {}) == []


def test_correction_message_lists_unsupported_claims() -> None:
    message = correction_message(verify(ORIGINAL_BUG + " C'est fait.", [], DOMAINS))

    assert message is not None
    assert "l'événement est ajouté à ton agenda." in message
    assert "calendar.write" in message
    assert "une action d’écriture" in message


def test_correction_message_is_none_when_everything_is_proved() -> None:
    assert correction_message(verify(ORIGINAL_BUG, [receipt("calendar.write")], DOMAINS)) is None
    assert correction_message(verify("Bonjour !", [], DOMAINS)) is None
