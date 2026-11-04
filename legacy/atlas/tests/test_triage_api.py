import time

import pytest
from fastapi.testclient import TestClient

from app.demo import demo_llm
from app.main import create_app
from tests.conftest import _TRIAGER


@pytest.mark.parametrize("text,effort,group", [
    ("salut", "light", None),
    ("merci beaucoup", "light", None),
    ("ajoute un rdv avec Karim mardi 9h", "standard", "calendar"),
    ("ajoute à ma todo : relancer la RH", "standard", "tasks"),
    ("prépare un mail pour remercier la recruteuse", "standard", "email"),
    ("compare pgvector et Qdrant pour mon projet et recommande-m'en un", "deep", None),
])
def test_triage(text, effort, group):
    t = _TRIAGER(text)
    assert t.effort == effort
    if group:
        assert group in t.groups


def test_triage_is_fast():
    t0 = time.perf_counter()
    for _ in range(100):
        _TRIAGER("ajoute un rdv demain à 14h")
    assert (time.perf_counter() - t0) / 100 < 0.01  # < 10 ms par message


@pytest.fixture
def client(store, tmp_path, monkeypatch):
    monkeypatch.setattr("app.config.settings.notes_dir", str(tmp_path / "notes"))
    return TestClient(create_app(store=store, llm=demo_llm(), sync_post=True))


def test_demo_end_to_end_liar_gets_corrected(client):
    r = client.post("/api/chat", json={"message": "Ajoute un rdv demain à 14h avec Karim"}).json()
    assert r["verification"]["verdict"] == "corrected"
    assert r["receipts"][0]["tool"] == "calendar_add_event" and r["receipts"][0]["status"] == "ok"
    s = client.get("/api/stats").json()
    assert s["false_claims_caught"] == 1 and s["verdicts"]["corrected"] == 1
    detail = client.get(f"/api/sessions/{r['session_id']}").json()
    assert len(detail["messages"]) == 2


def test_email_approval_flow(client):
    r = client.post("/api/chat", json={"message": "Prépare un mail à karim@exemple.fr pour confirmer jeudi"}).json()
    assert r["receipts"][0]["status"] == "pending_approval"
    pending = client.get("/api/outbox").json()
    assert len(pending) == 1
    oid = pending[0]["id"]
    assert client.post(f"/api/outbox/{oid}/approve").json()["status"] == "sent"
    assert client.post(f"/api/outbox/{oid}/approve").status_code == 409
    assert client.get("/api/outbox").json() == []


def test_remember_then_recall(client):
    client.post("/api/chat", json={"message": "Retiens que mon manager s'appelle Karim"})
    facts = client.get("/api/memory/facts").json()
    assert facts and "Karim" in facts[0]["content"]
    r = client.post("/api/chat", json={"message": "Comment s'appelle mon manager ?"}).json()
    assert "Karim" in r["reply"] and "[fact#" in r["reply"]


def test_index_served(client):
    assert "ATLAS" in client.get("/").text
