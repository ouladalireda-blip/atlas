import pytest

from app.verifier import detect_claims, verify


def cats(text):
    return [c.category for c in detect_claims(text)]


@pytest.mark.parametrize("text,expected", [
    ("Confirmé, l'événement est ajouté à ton agenda.", ["calendar"]),
    ("Confirmed, the event is added to your calendar.", ["calendar"]),
    ("Le rendez-vous a été créé pour jeudi 14h.", ["calendar"]),
    ("J'ai ajouté la tâche « relancer Capgemini » pour vendredi.", ["tasks"]),
    ("Mail envoyé à Karim !", ["email_send"]),
    ("I've sent the email to Karim.", ["email_send"]),
    ("Ton brouillon de mail est prêt, en attente de ton approbation.", ["email_draft"]),
    ("C'est noté, je m'en souviendrai.", ["memory"]),
    ("J'ai écrit une note sur Karim dans people/karim.md.", ["notes"]),
    ("C'est fait !", ["generic"]),
])
def test_detects_completion_claims(text, expected):
    assert cats(text) == expected


@pytest.mark.parametrize("text", [
    "Je vais ajouter le rdv à ton agenda.",
    "Je n'ai pas pu ajouter le rdv à ton agenda.",
    "Veux-tu que j'ajoute ce rendez-vous à ton agenda ?",
    "Tu as 3 événements cette semaine : lundi, mardi, jeudi.",
    "Voici le texte : on peut ajouter un événement via l'agenda partagé.",
    "L'ajout à l'agenda a échoué : la date est invalide.",
    "I'll add it to your calendar once you confirm the time.",
    "```\nevent added to calendar\n```",
])
def test_ignores_non_claims(text):
    assert cats(text) == []


def test_draft_is_not_a_send():
    receipts = [{"action_id": 1, "effect": "email.draft", "status": "pending_approval"}]
    assert verify("Brouillon de mail prêt, il attend ton accord.", receipts).ok
    v = verify("C'est bon, le mail est envoyé à Karim.", receipts)
    assert not v.ok and v.unsupported[0].category == "email_send"


def test_error_receipt_proves_nothing():
    receipts = [{"action_id": 7, "effect": "calendar.write", "status": "error"}]
    assert not verify("Le rdv est ajouté à ton agenda.", receipts).ok


def test_supported_claim_points_to_receipt():
    receipts = [{"action_id": 42, "effect": "calendar.write", "status": "ok"}]
    v = verify("Le rdv est ajouté à ton agenda.", receipts)
    assert v.ok and v.supported_by == {"calendar": [42]}


def test_wrong_kind_of_receipt_does_not_count():
    receipts = [{"action_id": 3, "effect": "tasks.write", "status": "ok"}]
    assert not verify("C'est ajouté à ton agenda.", receipts).ok
