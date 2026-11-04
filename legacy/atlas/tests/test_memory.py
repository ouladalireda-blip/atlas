import pytest

from app.llm import ScriptedLLM
from tests.conftest import say


def test_fact_dedup_updates_instead_of_duplicating(memory, store):
    a = memory.add_fact("Le manager de l'utilisateur s'appelle Karim.", "user")
    b = memory.add_fact("Le manager de l'utilisateur s'appelle Karim !", "user")
    assert a["status"] == "created" and b["status"] == "updated" and a["id"] == b["id"]
    assert store.one("select count(*) as n from facts")["n"] == 1


def test_recall_spans_layers_with_sources(memory, store):
    memory.add_fact("L'utilisateur prépare une alternance en data analyst.", "user")
    memory.write_note("people/karim.md", "# Karim\nManager exigeant, aime les réponses chiffrées.")
    store.ensure_session("s1")
    mid = store.add_message("s1", "t1", "user", "Le code Wi-Fi de l'IFIS est TOURNESOL42")
    memory.index_message(mid, "user", "Le code Wi-Fi de l'IFIS est TOURNESOL42")

    assert memory.recall("alternance data")[0].source.startswith("fact#")
    assert memory.recall("qui est Karim ?")[0].source == "note:people/karim.md"
    hits = memory.recall("TOURNESOL42")  # couche exacte : mot rare
    assert any(h.source == f"msg#{mid}" for h in hits)


def test_forget_removes_fact_and_vector(memory, store):
    f = memory.add_fact("L'utilisateur déteste la coriandre.", "user")
    assert memory.forget_fact(f["id"])
    assert store.one("select count(*) as n from chunks where kind = 'fact'")["n"] == 0
    assert not memory.forget_fact(f["id"])


def test_notes_cannot_escape_folder(memory):
    with pytest.raises(ValueError):
        memory.notes.write("../../etc/passwd", "x")


def test_note_append(memory):
    memory.write_note("letters/2026-11", "Premier mot.")
    memory.write_note("letters/2026-11", "Deuxième mot.", mode="append")
    assert memory.notes.read("letters/2026-11.md") == "Premier mot.\n\nDeuxième mot."


def test_fact_extraction_labels_speaker(memory):
    llm = ScriptedLLM([say('{"facts": [{"fact": "L\'utilisateur habite à Noisy-le-Grand.", "speaker": "user"},'
                           ' {"fact": "x", "speaker": "user"}]}')])
    saved = memory.extract_facts(llm, "m", "j'habite à Noisy-le-Grand", "Noté.", "s1", None)
    assert len(saved) == 1 and saved[0]["status"] == "created"


def test_fact_extraction_survives_garbage(memory):
    assert memory.extract_facts(ScriptedLLM([say("pas du json")]), "m", "a", "b", "s1", None) == []
