"""Tests du lexique de verbes : une seule source de vérité pour les formes et les classes."""

import re

import pytest

from atlas.core import lexicon
from atlas.core.claims.rules import detect_claims
from atlas.core.lexicon import (
    LEXICON,
    PARTICIPLE_CLASS,
    PRESENT_EN_CLASS,
    PRESENT_FR_CLASS,
    PROGRESSIVE_EN_CLASS,
    VERB_CLASSES,
    Domain,
)

DOMAINS = {
    "demo": Domain(
        nouns=("objet",),
        actions={"write": "demo.write", "delete": "demo.delete", "send": "demo.send"},
    ),
}


def effects(reply: str) -> list[str | None]:
    return [claim.effect for claim in detect_claims(reply, DOMAINS)]


def test_the_core_knows_exactly_three_classes() -> None:
    assert VERB_CLASSES == {"write", "delete", "send"}
    assert set(LEXICON) == VERB_CLASSES


@pytest.mark.parametrize(
    ("verb_class", "forms"),
    [
        ("write", ["ajouté", "ajoutée", "ajoutés", "ajoutées", "créé", "créée", "créés", "créées"]),
        ("write", ["mis", "mise", "mises", "inscrit", "inscrite", "inscrits", "inscrites"]),
        (
            "write",
            ["programmé", "planifiée", "réservés", "bloquées", "calé", "noté", "enregistrées"],
        ),
        ("write", ["écrit", "écrite", "complété", "complétée", "marquée", "cochée", "confirmé"]),
        ("write", ["rayé", "rayée", "rayés", "rayées"]),  # rayer = marquer comme fait
        ("delete", ["supprimé", "supprimée", "supprimés", "supprimées", "effacé", "annulée"]),
        ("delete", ["retiré", "retirée", "retirés", "retirées", "enlevé", "détruit", "détruites"]),
        ("send", ["envoyé", "envoyée", "envoyés", "envoyées", "expédié", "relancée"]),
        ("send", ["transmis", "transmise", "transmises", "parti", "partie", "partis", "parties"]),
        ("send", ["répondu", "répondue", "prévenu", "prévenues", "adressé"]),
        ("write", ["added", "created", "scheduled", "booked", "set", "put", "made", "written"]),
        (
            "write",
            ["wrote", "saved", "updated", "moved", "marked", "logged", "planned", "completed"],
        ),
        ("delete", ["deleted", "removed", "cancelled", "canceled", "erased", "cleared", "dropped"]),
        ("send", ["sent", "emailed", "mailed", "forwarded", "replied", "delivered", "notified"]),
    ],
)
def test_participles_in_all_genders_numbers_and_irregular_forms(
    verb_class: str, forms: list[str]
) -> None:
    for form in forms:
        assert PARTICIPLE_CLASS.get(form) == verb_class, form


@pytest.mark.parametrize(
    ("verb_class", "forms"),
    [
        ("write", ["ajoute", "crée", "programme", "planifie", "mets", "inscris", "écris", "note"]),
        ("delete", ["supprime", "efface", "annule", "retire", "détruis", "enlève"]),
        ("write", ["raye"]),
        ("send", ["envoie", "transmets", "réponds", "expédie", "relance", "transfère"]),
    ],
)
def test_first_person_present_french(verb_class: str, forms: list[str]) -> None:
    for form in forms:
        assert PRESENT_FR_CLASS.get(form) == verb_class, form


@pytest.mark.parametrize(
    ("table", "verb_class", "forms"),
    [
        (PRESENT_EN_CLASS, "write", ["add", "create", "schedule", "book", "write", "update"]),
        (PRESENT_EN_CLASS, "delete", ["delete", "remove", "cancel", "erase"]),
        (PRESENT_EN_CLASS, "send", ["send", "email", "forward", "reply"]),
        (PROGRESSIVE_EN_CLASS, "write", ["adding", "scheduling", "setting", "putting", "making"]),
        (PROGRESSIVE_EN_CLASS, "delete", ["deleting", "removing", "cancelling", "canceling"]),
        (PROGRESSIVE_EN_CLASS, "send", ["sending", "emailing", "replying"]),
    ],
)
def test_english_present_and_progressive(
    table: dict[str, str], verb_class: str, forms: list[str]
) -> None:
    for form in forms:
        assert table.get(form) == verb_class, form


def test_passe_compose_is_recognised_through_the_participle() -> None:
    assert effects("J'ai supprimé l'objet.") == ["demo.delete"]
    assert effects("Elle a été supprimée avec l'objet.") == ["demo.delete"]
    assert effects("Nous avons transmis l'objet.") == ["demo.send"]
    assert effects("Il a mis l'objet à jour.") == ["demo.write"]


@pytest.mark.parametrize(
    "word",
    [
        "utilise lis consulte vérifie regarde cherche trouve gère ouvre montre affiche suis sais",
        "chargé organisé partagé vérifié consulté trouvé demandé souhaité désolé été échoué",
        "used read checked looked viewed searched reviewed asked based sorted loaded shared",
    ],
)
def test_reading_and_state_words_are_not_in_the_lexicon(word: str) -> None:
    every_form = (
        set(PARTICIPLE_CLASS)
        | set(PRESENT_FR_CLASS)
        | set(PRESENT_EN_CLASS)
        | set(PROGRESSIVE_EN_CLASS)
    )

    assert not (set(word.split()) & every_form)


def test_forms_are_normalised_for_the_verifier() -> None:
    for table in (PARTICIPLE_CLASS, PRESENT_FR_CLASS, PRESENT_EN_CLASS, PROGRESSIVE_EN_CLASS):
        for form in table:
            assert form == form.lower() and re.fullmatch(r"\w+", form), form


def test_every_class_has_every_kind_of_form() -> None:
    for verb in LEXICON.values():
        assert verb.participles and verb.present_fr and verb.present_en and verb.progressive_en


def test_each_form_belongs_to_a_single_class() -> None:
    for table in (PARTICIPLE_CLASS, PRESENT_FR_CLASS, PRESENT_EN_CLASS, PROGRESSIVE_EN_CLASS):
        assert all(table[form] in VERB_CLASSES for form in table)


def test_an_ambiguous_lexicon_is_refused(monkeypatch: pytest.MonkeyPatch) -> None:
    clash = lexicon.VerbClass(
        name="clash",
        participles=frozenset({"ajouté"}),
        present_fr=frozenset(),
        present_en=frozenset(),
        progressive_en=frozenset(),
    )
    monkeypatch.setattr(lexicon, "LEXICON", {**LEXICON, "clash": clash})

    with pytest.raises(ValueError, match="Lexique ambigu : « ajouté »"):
        lexicon._index("participles")


def test_domain_is_a_plain_value() -> None:
    domain = Domain(nouns=("a",), actions={"write": "x.write"})

    assert domain == Domain(nouns=("a",), actions={"write": "x.write"})


def test_raying_is_writing_not_deleting() -> None:
    assert not any(form.startswith("ray") for form in LEXICON["delete"].participles)
    assert "raye" not in LEXICON["delete"].present_fr
