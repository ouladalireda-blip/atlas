"""API FastAPI + interface web."""
from __future__ import annotations

import uuid
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, HTTPException
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field

from .agent import Agent
from .config import ROOT, settings
from .db import Store
from .llm import OpenRouterClient

WEB = ROOT / "web"


class ChatIn(BaseModel):
    message: str = Field(min_length=1, max_length=8000)
    session_id: str | None = None


def create_app(store: Store | None = None, llm=None, sync_post: bool = False) -> FastAPI:
    store = store or Store()
    demo = False
    if llm is None:
        if settings.openrouter_api_key:
            llm = OpenRouterClient()
        else:
            from .demo import demo_llm
            llm, demo = demo_llm(), True
    agent = Agent(store, llm)
    agent.memory.reindex_notes()

    app = FastAPI(title="ATLAS", version="0.1.0")
    app.state.agent = agent

    @app.get("/", include_in_schema=False)
    def index():
        return FileResponse(WEB / "index.html")

    @app.get("/api/health")
    def health():
        return {"ok": True, "demo": demo, "backend": "postgres" if store.pg else "sqlite",
                "models": {"fast": settings.model_fast, "main": settings.model_main, "deep": settings.model_deep}}

    @app.post("/api/chat")
    def chat(body: ChatIn, background: BackgroundTasks):
        sid = body.session_id or uuid.uuid4().hex[:10]
        result = agent.run(sid, body.message.strip())
        if sync_post:
            result["facts_saved"] = agent.post_turn(result)
        else:
            background.add_task(agent.post_turn, dict(result))
        result.pop("_post", None)
        return result

    @app.get("/api/sessions")
    def sessions():
        return store.query(
            "select s.id, s.title, s.created_at, count(m.id) as messages from sessions s "
            "left join messages m on m.session_id = s.id group by s.id, s.title, s.created_at "
            "order by s.created_at desc limit 50")

    @app.get("/api/sessions/{sid}")
    def session_detail(sid: str):
        msgs = store.query("select id, turn_id, role, content, created_at from messages where session_id = ? order by id",
                           (sid,))
        turns = {}
        for t in {m["turn_id"] for m in msgs}:
            v = store.one("select verdict, claims, unsupported from verifications where turn_id = ? order by id desc",
                          (t,))
            acts = store.query("select id as action_id, tool, effect, status, result from actions where turn_id = ?",
                               (t,))
            turns[t] = {"verification": v, "receipts": acts}
        return {"messages": msgs, "turns": turns}

    @app.get("/api/outbox")
    def outbox(status: str = "pending_approval"):
        return store.query("select * from outbox where status = ? order by id desc", (status,))

    @app.post("/api/outbox/{oid}/{decision}")
    def decide(oid: int, decision: str):
        if decision not in ("approve", "reject"):
            raise HTTPException(400, "approve ou reject")
        row = store.one("select * from outbox where id = ?", (oid,))
        if not row:
            raise HTTPException(404, "brouillon introuvable")
        if row["status"] != "pending_approval":
            raise HTTPException(409, f"déjà traité ({row['status']})")
        new = "sent" if decision == "approve" else "rejected"
        # Envoi simulé : brancher ici SMTP / Gmail API. Le reçu email.send n'existe qu'après approbation humaine.
        store.query("update outbox set status = ?, decided_at = current_timestamp where id = ?", (new, oid))
        if new == "sent":
            store.log_action("user", row["turn_id"] or "manual", "email_send", "email.send",
                             {"outbox_id": oid}, {"to": row["recipient"]}, "sent")
        return {"id": oid, "status": new}

    @app.get("/api/memory/facts")
    def facts():
        return store.query("select id, content, speaker, created_at, updated_at from facts order by id desc limit 200")

    @app.delete("/api/memory/facts/{fid}")
    def forget(fid: int):
        if not agent.memory.forget_fact(fid):
            raise HTTPException(404, "fait introuvable")
        return {"forgotten": fid}

    @app.get("/api/memory/search")
    def search(q: str):
        return [m.as_dict() for m in agent.memory.recall(q, k=10)]

    @app.get("/api/notes")
    def notes():
        return agent.memory.notes.list()

    @app.get("/api/notes/{path:path}")
    def note(path: str):
        try:
            text = agent.memory.notes.read(path)
        except ValueError:
            raise HTTPException(400, "chemin invalide")
        if text is None:
            raise HTTPException(404, "note introuvable")
        return {"path": path, "content": text}

    @app.get("/api/stats")
    def stats():
        day = store.one(
            "select count(*) as turns, coalesce(sum(cost_usd),0) as cost_usd, coalesce(sum(prompt_tokens),0) as prompt, "
            "coalesce(sum(cached_tokens),0) as cached from turns where date(created_at) = date(current_timestamp)") or {}
        verdicts = store.query("select verdict, count(*) as n from verifications where verdict != 'rejected_draft' "
                               "group by verdict")
        caught = store.one("select count(*) as n from verifications where verdict = 'rejected_draft'") or {"n": 0}
        efforts = store.query("select effort, count(*) as n from turns group by effort")
        prompt = day.get("prompt") or 0
        return {
            "today": {"turns": day.get("turns", 0), "cost_usd": round(float(day.get("cost_usd") or 0), 4),
                      "cache_hit_rate": round((day.get("cached") or 0) / prompt, 3) if prompt else None},
            "verdicts": {v["verdict"]: v["n"] for v in verdicts},
            "false_claims_caught": caught["n"],
            "efforts": {e["effort"]: e["n"] for e in efforts},
        }

    return app
# Lancement : uvicorn app.main:create_app --factory --reload
