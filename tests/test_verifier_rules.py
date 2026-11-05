"""Tests des règles du vérificateur : présent à la 1re personne, formules de confirmation, ne…que.

Les phrases diffèrent de celles de evals/verifier_cases.yaml, qui servent à mesurer ces règles.
"""

import pytest

from atlas.core import verifier
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


def receipt(effect: str | None) -> Receipt:
    return Receipt(id=1, turn_id="t1", tool="x", effect=effect, args={}, status=ReceiptStatus.OK)


def effects(reply: str) -> list[str | None]:
    return [claim.effect for claim in detect_claims(reply, DOMAINS)]


@pytest.mark.parametrize(
    ("reply", "expected"),
    [
        ("J'ajoute le rendez-vous à ton agenda.", ["calendar.write"]),
        ("Je te l'envoie par mail.", ["email.send"]),
        ("Je l'annule dans ton agenda.", ["calendar.delete"]),  # classe non déclarée
        ("Je mets le rendez-vous dans ton agenda.", ["calendar.write"]),
        ("Voilà, je crée l'événement.", ["calendar.write"]),
        ("I add the event to your calendar.", ["calendar.write"]),
        ("I'm adding the event now.", ["calendar.write"]),
        ("I am sending the email.", ["email.send"]),
        ("I just cancel the event.", ["calendar.delete"]),  # classe non déclarée
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

    assert verify(reply, [], DOMAINS).unsupported
    assert verify(reply, [receipt("calendar.write")], DOMAINS).unsupported
    assert not verify(reply, [receipt("email.send")], DOMAINS).unsupported


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
        ("Je n'ai supprimé que l'événement de Sam.", ["calendar.delete"]),
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
    "tasks": Domain(
        nouns=("tâche", "task"),
        actions={"write": "tasks.write", "delete": "tasks.delete"},
    ),
}
BOTH = {**DOMAINS, **TASKS}


def task_effects(reply: str, domains: dict[str, Domain] = TASKS) -> list[str | None]:
    return [claim.effect for claim in detect_claims(reply, domains)]


def test_a_delete_verb_next_to_a_task_is_tasks_delete() -> None:
    assert task_effects("La tâche est supprimée.") == ["tasks.delete"]
    assert task_effects("Je supprime la tâche 2.") == ["tasks.delete"]


def test_retiree_with_a_task_is_tasks_delete() -> None:
    assert task_effects("La tâche 4 a été retirée de ta liste.") == ["tasks.delete"]
    assert task_effects("J'ai retiré la tâche 4.") == ["tasks.delete"]
    assert task_effects("The task was removed.") == ["tasks.delete"]


def test_deleting_an_appointment_is_calendar_delete_never_tasks_delete() -> None:
    claims = detect_claims("J'ai supprimé le rendez-vous de lundi.", BOTH)

    assert [(claim.effect, claim.declared) for claim in claims] == [("calendar.delete", False)]


def test_the_same_verb_gives_the_effect_of_the_domain_of_the_nearest_noun() -> None:
    assert task_effects("La tâche est ajoutée.", BOTH) == ["tasks.write"]
    assert task_effects("L'événement est ajouté.", BOTH) == ["calendar.write"]
    assert task_effects("Le mail est supprimé.", BOTH) == ["email.delete"]


def test_a_verb_class_the_domain_does_not_declare_is_flagged_and_unprovable() -> None:
    claims = detect_claims("Le mail est supprimé.", DOMAINS)

    assert claims == [Claim("email.delete", "Le mail est supprimé.", declared=False)]
    # Même un reçu portant cet effet ne la prouve pas : aucun outil ne le déclare.
    result = verify("Le mail est supprimé.", [receipt("email.delete")], DOMAINS)
    assert result.unsupported == claims
    assert result.supported_by == {}
    message = correction_message(result)
    assert (
        message is not None and "email.delete" in message and "aucun outil ne la déclare" in message
    )


def test_an_undeclared_claim_is_never_attributed_to_another_domain() -> None:
    result = verify("Le rendez-vous est supprimé.", [receipt("tasks.delete")], BOTH)

    assert [c.effect for c in result.claims] == ["calendar.delete"]
    assert result.unsupported


def test_the_nearest_noun_decides_the_domain() -> None:
    # « mail » est à 2 mots de « ajouté », « événement » à 5 : l'objet le plus proche gagne.
    assert task_effects("J'ai ajouté un mail à l'événement.", BOTH) == ["email.write"]
    assert task_effects("J'ai ajouté l'événement avec un mail.", BOTH) == ["calendar.write"]


def test_a_tie_goes_to_the_noun_after_the_verb_then_to_the_first_domain() -> None:
    # À égale distance de chaque côté (1 mot), le nom qui suit le verbe l'emporte.
    assert task_effects("mail ajouté tâche", BOTH) == ["tasks.write"]
    domains = {
        "second": Domain(nouns=("objet",), actions={"write": "second.write"}),
        "first": Domain(nouns=("objet",), actions={"write": "first.write"}),
    }
    assert task_effects("objet ajouté", domains) == ["second.write"]  # ordre du dictionnaire


def test_a_verb_with_no_noun_nearby_claims_nothing() -> None:
    assert task_effects("Tout est validé.", BOTH) == []
    assert (
        task_effects("J'ai ajouté le mot de passe qui était demandé il y a longtemps.", TASKS) == []
    )
    # Un nom trop loin (plus de 6 mots) ne compte pas.
    assert task_effects("Ajouté : un, deux, trois, quatre, cinq, six, sept, la tâche.", TASKS) == []


def test_both_actions_in_one_sentence_need_both_receipts() -> None:
    effects = task_effects("J'ai ajouté la tâche et supprimé l'ancienne.")

    assert sorted(str(effect) for effect in effects) == ["tasks.delete", "tasks.write"]


def test_the_confirmation_verb_of_a_formula_is_not_an_action_on_the_object() -> None:
    # « confirme » est un verbe write, mais ici il annonce l'affirmation qui suit.
    assert effects("Je te confirme que le mail est parti.") == ["email.send"]
    assert effects("I can confirm the email was sent.") == ["email.send"]
    assert effects("I confirm that the email was sent.") == ["email.send"]
    # Hors formule, c'est une action : « je confirme le rendez-vous ».
    assert effects("Je confirme le rendez-vous.") == ["calendar.write"]


def test_a_verb_added_to_the_lexicon_serves_every_domain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert task_effects("La tâche est zorblée.", BOTH) == []
    assert task_effects("L'événement est zorblé.", BOTH) == []
    monkeypatch.setattr(
        verifier,
        "PARTICIPLE_CLASS",
        {**verifier.PARTICIPLE_CLASS, "zorblé": "write", "zorblée": "write"},
    )

    assert task_effects("La tâche est zorblée.", BOTH) == ["tasks.write"]
    assert task_effects("L'événement est zorblé.", BOTH) == ["calendar.write"]
