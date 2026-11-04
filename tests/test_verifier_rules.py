"""Tests des règles du vérificateur : présent à la 1re personne, formules de confirmation, ne…que.

Les phrases diffèrent de celles de evals/verifier_cases.yaml, qui servent à mesurer ces règles.
"""

import pytest

from atlas.core.receipts import Receipt, ReceiptStatus
from atlas.core.verifier import detect_claims, verify

VOCABULARY = {
    "calendar.write": ["événement", "agenda", "rendez-vous", "event", "calendar"],
    "email.send": ["mail", "e-mail", "email"],
}


def receipt(effect: str | None) -> Receipt:
    return Receipt(id=1, turn_id="t1", tool="x", effect=effect, args={}, status=ReceiptStatus.OK)


def effects(reply: str) -> list[str | None]:
    return [claim.effect for claim in detect_claims(reply, VOCABULARY)]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("J'ajoute le rendez-vous à ton agenda.", ["calendar.write"]),
        ("Je te l'envoie par mail.", ["email.send"]),
        ("Je l'annule dans ton agenda.", ["calendar.write"]),
        ("Je mets le rendez-vous dans ton agenda.", ["calendar.write"]),
        ("Voilà, je crée l'événement.", ["calendar.write"]),
        ("I add the event to your calendar.", ["calendar.write"]),
        ("I'm adding the event now.", ["calendar.write"]),
        ("I am sending the email.", ["email.send"]),
        ("I just cancel the event.", ["calendar.write"]),
    ],
)
def test_first_person_present_write_verb_is_a_claim(reply: str, expected: list[str]) -> None:
    assert effects(reply) == expected


@pytest.mark.parametrize(
    "reply",
    [
        "J'utilise ton agenda.",
        "Je lis ton agenda.",
        "Je consulte ton agenda.",
        "Je gère ton agenda.",
        "I use your calendar.",
        "I read your calendar.",
        "I'm checking your calendar.",
        "Je n'ajoute pas l'événement.",
        "Je vais ajouter l'événement.",
        "Je peux ajouter l'événement.",
        "Il faut que j'ajoute l'événement.",
        "Veux-tu que j'ajoute l'événement ?",
        "J'ajoute l'événement ?",
        "Si j'ajoute l'événement, tu seras prévenu.",
        "I'm not adding the event.",
        "I'm going to add the event.",
        "I can add the event.",
        "Ajoute l'événement.",
        "Tu ajoutes l'événement.",
        "J'ajoute un peu de sel dans la sauce de ma recette préférée.",
    ],
)
def test_present_tense_that_is_not_a_claim(reply: str) -> None:
    assert effects(reply) == []


def test_first_person_present_needs_a_receipt_of_the_right_effect() -> None:
    reply = "J'envoie le mail à Sam."

    assert verify(reply, [], VOCABULARY).unsupported
    assert verify(reply, [receipt("calendar.write")], VOCABULARY).unsupported
    assert not verify(reply, [receipt("email.send")], VOCABULARY).unsupported


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Je peux confirmer que le mail est envoyé.", ["email.send"]),
        ("Je peux te confirmer que le mail est envoyé.", ["email.send"]),
        ("Je te confirme que l'événement est créé.", ["calendar.write"]),
        ("Je vous confirme que le mail est parti.", ["email.send"]),
        ("Je t'assure que l'événement est ajouté.", ["calendar.write"]),
        ("Je peux t'assurer que l'agenda est mis à jour.", ["calendar.write"]),
        ("I can confirm the event was created.", ["calendar.write"]),
        ("I can confirm that the email was sent.", ["email.send"]),
        ("I can assure you the event was added.", ["calendar.write"]),
    ],
)
def test_confirmation_formulas_are_claims_despite_the_auxiliary(
    reply: str, expected: list[str]
) -> None:
    assert effects(reply) == expected


@pytest.mark.parametrize(
    "reply",
    [
        "Je ne peux pas confirmer que le mail est envoyé.",
        "Je ne peux pas t'assurer que l'événement soit ajouté.",
        "Je ne te confirme pas que le mail est envoyé.",
        "Je peux confirmer que le mail sera envoyé.",
        "Je peux confirmer que le mail n'est pas envoyé.",
        "I cannot confirm the event was created.",
        "I can't confirm the event was created.",
        "I can confirm the event will be created.",
        "Peux-tu me confirmer que le mail est envoyé ?",
        "Dès que je peux confirmer que le mail est envoyé, je te préviens.",
    ],
)
def test_confirmation_formulas_keep_negation_future_and_questions(reply: str) -> None:
    assert effects(reply) == []


def test_the_auxiliary_exemption_is_limited_to_the_confirmation_formula() -> None:
    # Seule la formule de confirmation est exemptée : l'autre « peux » bloque toujours.
    assert effects("Je peux ajouter l'événement et je te confirme que le mail est parti.") == [
        "email.send"
    ]
    assert effects("Je peux envoyer le mail.") == []


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("Je n'ai supprimé que l'événement de Sam.", ["calendar.write"]),
        ("Il n'a été envoyé que le mail de Sam.", ["email.send"]),
        ("Je n'ai modifié qu'un rendez-vous.", ["calendar.write"]),
        ("Je ne l'ai ajouté que dans ton agenda.", ["calendar.write"]),
    ],
)
def test_ne_que_is_a_restriction_not_a_negation(reply: str, expected: list[str]) -> None:
    assert effects(reply) == expected


@pytest.mark.parametrize(
    "reply",
    [
        "Je n'ai pas ajouté que l'événement est prévu.",
        "Je n'ai jamais ajouté l'événement que tu cites.",
        "Je n'ai ajouté aucun événement que tu aies cité.",
        "Je n'ai rien envoyé que Sam attendait.",
        "Je n'ai plus ajouté l'événement que tu cites.",
        "Je n'ai pas envoyé le mail.",
    ],
)
def test_ne_with_another_negation_stays_a_negation(reply: str) -> None:
    assert effects(reply) == []


def test_ne_que_does_not_lift_other_blockers() -> None:
    assert effects("Je ne peux ajouter que l'événement.") == []
    assert effects("Je n'ajouterai que l'événement.") == []


TASKS = {
    "tasks.write": ["tâche", "task"],
    "tasks.delete": ["supprimé", "supprimée", "supprime"],
}


def task_effects(reply: str) -> list[str | None]:
    return [claim.effect for claim in detect_claims(reply, TASKS)]


def test_a_verb_that_is_a_vocabulary_word_names_its_own_effect() -> None:
    # « supprimée » est un mot de tasks.delete : le nom « tâche » ne suffit pas à viser tasks.write.
    assert task_effects("La tâche est supprimée.") == ["tasks.delete"]
    assert task_effects("Je supprime la tâche 2.") == ["tasks.delete"]


def test_nouns_alone_still_match_every_effect_they_belong_to() -> None:
    assert task_effects("La tâche est ajoutée.") == ["tasks.write"]
    shared = {"a.write": ["tâche"], "a.delete": ["tâche"]}
    effects = [claim.effect for claim in detect_claims("La tâche est ajoutée.", shared)]
    assert sorted(str(effect) for effect in effects) == ["a.delete", "a.write"]


def test_both_actions_in_one_sentence_need_both_receipts() -> None:
    effects = task_effects("J'ai ajouté la tâche et supprimé l'ancienne.")

    assert sorted(str(effect) for effect in effects) == ["tasks.delete", "tasks.write"]
