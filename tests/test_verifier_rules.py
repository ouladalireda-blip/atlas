"""Tests des règles du vérificateur : présent à la 1re personne, formules de confirmation, ne…que.

Les phrases diffèrent de celles de evals/verifier_cases.yaml, qui servent à mesurer ces règles.
"""

import pytest

from atlas.core import verifier
from atlas.core.lexicon import PARTICIPLE_CLASS, PRESENT_FR_CLASS, Domain
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


def test_a_noun_beyond_six_words_does_not_count() -> None:
    far = "Ajouté : un, deux, trois, quatre, cinq, six, sept, la tâche."

    # Pas de nom à portée : le repli de classe s'applique (classe write déclarée par tasks seul).
    assert task_effects(far, TASKS) == ["tasks.write"]
    # La classe delete n'est déclarée par personne dans DOMAINS : rien à affirmer.
    assert (
        task_effects("Supprimé : un, deux, trois, quatre, cinq, six, sept, le mail.", DOMAINS) == []
    )


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


# --- repli : un verbe d'action sans nom de domaine à portée --------------------------------------


def claim_list(reply: str, domains: dict[str, Domain] = BOTH) -> list[Claim]:
    return detect_claims(reply, domains)


def test_a_verb_alone_takes_the_effect_of_the_only_domain_that_declares_its_class() -> None:
    # Seul le domaine email déclare la classe send.
    assert effects("C'est envoyé !") == ["email.send"]
    assert task_effects("C'est envoyé !", BOTH) == ["email.send"]
    assert effects("Envoyé.") == ["email.send"]
    assert effects("J'envoie.") == ["email.send"]
    assert effects("I sent it.") == ["email.send"]
    # Seul tasks déclare delete dans BOTH.
    assert task_effects("Supprimé !", BOTH) == ["tasks.delete"]


def test_a_class_declared_by_several_domains_gives_a_generic_claim_of_the_class() -> None:
    (claim,) = claim_list("C'est ajouté !")

    assert claim.effect == "*.write"
    assert claim.declared and set(claim.any_of) == {"calendar.write", "tasks.write"}


def test_a_generic_class_claim_is_proved_by_any_ok_receipt_of_that_class() -> None:
    reply = "C'est ajouté !"

    assert not verify(reply, [receipt("calendar.write")], BOTH).unsupported
    assert not verify(reply, [receipt("tasks.write")], BOTH).unsupported
    # Un reçu d'une autre classe, ou aucun reçu, ne la prouve pas.
    assert verify(reply, [receipt("tasks.delete")], BOTH).unsupported
    assert verify(reply, [receipt("email.send")], BOTH).unsupported
    assert verify(reply, [], BOTH).unsupported


def test_a_receipt_that_is_not_ok_does_not_prove_a_generic_claim() -> None:
    failed = Receipt(
        id=1, turn_id="t", tool="x", effect="tasks.write", args={}, status=ReceiptStatus.ERROR
    )

    assert verify("C'est ajouté !", [failed], BOTH).unsupported


def test_the_correction_message_for_a_generic_claim_lists_the_acceptable_effects() -> None:
    message = correction_message(verify("C'est ajouté !", [], BOTH))

    assert message is not None
    assert "sans objet précisé" in message
    assert "calendar.write" in message and "tasks.write" in message


@pytest.mark.parametrize(
    "reply",
    [
        "Ce n'est pas envoyé.",
        "Je n'ai pas envoyé.",
        "Rien n'a été envoyé.",
        "Je n'ai jamais ajouté.",
        "I did not send it.",
        "I haven't sent it.",
        "Je vais envoyer.",
        "Ce sera envoyé demain.",
        "Je peux l'envoyer.",
        "Dès que tu valides, c'est envoyé.",
        "Quand tu me le diras, je l'envoie.",
        "Si tu veux, j'envoie.",
        "Once approved, it is sent.",
        "C'est envoyé ?",
        "Est-ce envoyé ?",
        "Veux-tu que ce soit envoyé ?",
    ],
)
def test_a_lone_verb_with_a_blocking_marker_claims_nothing(reply: str) -> None:
    assert effects(reply) == []
    assert task_effects(reply, BOTH) == []


def test_a_lone_verb_before_a_negation_is_still_a_claim() -> None:
    # Le marqueur annule ce qui le suit, pas ce qui le précède.
    assert effects("Envoyé sans problème.") == ["email.send"]


def test_a_noun_in_range_keeps_priority_over_the_fallback() -> None:
    # Le nom est là : pas de repli. calendar ne déclare pas delete, donc c'est calendar.delete,
    # non déclaré, et surtout pas tasks.delete (la classe n'est déclarée que par tasks).
    claims = claim_list("L'événement est supprimé.")

    assert [(c.effect, c.declared) for c in claims] == [("calendar.delete", False)]
    assert task_effects("La tâche est ajoutée.", BOTH) == ["tasks.write"]
    assert task_effects("Le mail est envoyé.", BOTH) == ["email.send"]


def test_a_class_nobody_declares_claims_nothing_even_without_a_noun() -> None:
    only_write = {"calendar": DOMAINS["calendar"]}

    assert task_effects("C'est envoyé !", only_write) == []
    assert task_effects("Supprimé !", only_write) == []


def test_raying_a_task_is_marking_it_done() -> None:
    assert task_effects("J'ai rayé la tâche.") == ["tasks.write"]
    assert task_effects("La tâche 3 est rayée.") == ["tasks.write"]
    assert task_effects("Je raye la tâche 3.") == ["tasks.write"]
    assert PARTICIPLE_CLASS["rayé"] == "write"
    assert PRESENT_FR_CLASS["raye"] == "write"


def test_an_unrelated_sentence_with_an_action_verb_is_a_known_false_alarm() -> None:
    # Coût assumé du repli : sans objet de domaine à portée, tout verbe d'action de la classe
    # déclarée par un domaine se lit comme une affirmation (ADR 0005).
    assert effects("J'ajoute un peu de sel dans la sauce.") == ["calendar.write"]
