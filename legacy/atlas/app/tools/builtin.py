"""Les outils d'ATLAS. Chacun existe parce qu'un usage réel le demandait."""
from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

from .registry import Registry, ToolContext

registry = Registry()
S = {"type": "string"}
I = {"type": "integer"}


def _parse_dt(value: str, tz: str) -> datetime:
    dt = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=ZoneInfo(tz))
    return dt.astimezone(timezone.utc)


def _utc(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S+00:00")


def _local(value: str | None, tz: str) -> str | None:
    if not value:
        return None
    dt = datetime.fromisoformat(str(value).replace(" ", "T").replace("Z", "+00:00"))
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(ZoneInfo(tz)).strftime("%a %d/%m/%Y %H:%M")


# ---------------- core ----------------
@registry.register("get_datetime", "Date et heure actuelles dans le fuseau de l'utilisateur.", {}, "core")
def get_datetime(ctx: ToolContext) -> dict:
    now = datetime.now(ZoneInfo(ctx.tz))
    return {"now": now.isoformat(timespec="minutes"), "weekday": now.strftime("%A"), "timezone": ctx.tz}


# ---------------- calendrier ----------------
@registry.register(
    "calendar_add_event", "Ajoute un événement à l'agenda. start/end au format ISO (ex 2026-11-03T14:00).",
    {"title": S, "start": S, "end": S, "duration_minutes": I}, "calendar", effect="calendar.write",
    required=["title", "start"])
def calendar_add_event(ctx: ToolContext, title: str, start: str, end: str | None = None,
                       duration_minutes: int | None = None) -> dict:
    s = _parse_dt(start, ctx.tz)
    e = _parse_dt(end, ctx.tz) if end else s + timedelta(minutes=duration_minutes or 60)
    if e <= s:
        raise ValueError("la fin doit être après le début")
    clash = ctx.store.query(
        "select id, title, starts_at from events where starts_at < ? and coalesce(ends_at, starts_at) > ?",
        (_utc(e), _utc(s)))
    eid = ctx.store.insert("insert into events (title, starts_at, ends_at) values (?, ?, ?)",
                           (title, _utc(s), _utc(e)))
    return {"event_id": eid, "title": title, "start": _local(_utc(s), ctx.tz), "end": _local(_utc(e), ctx.tz),
            "conflicts": [{"id": c["id"], "title": c["title"]} for c in clash]}


@registry.register(
    "calendar_list_events", "Liste les événements entre deux dates ISO (par défaut : les 7 prochains jours).",
    {"start": S, "end": S}, "calendar")
def calendar_list_events(ctx: ToolContext, start: str | None = None, end: str | None = None) -> dict:
    s = _parse_dt(start, ctx.tz) if start else datetime.now(timezone.utc)
    e = _parse_dt(end, ctx.tz) if end else s + timedelta(days=7)
    rows = ctx.store.query(
        "select id, title, starts_at, ends_at from events where starts_at >= ? and starts_at < ? order by starts_at",
        (_utc(s), _utc(e)))
    return {"events": [{"id": r["id"], "title": r["title"], "start": _local(r["starts_at"], ctx.tz),
                        "end": _local(r["ends_at"], ctx.tz)} for r in rows]}


@registry.register("calendar_delete_event", "Supprime un événement par son id.", {"event_id": I}, "calendar",
                   effect="calendar.write", required=["event_id"])
def calendar_delete_event(ctx: ToolContext, event_id: int) -> dict:
    if not ctx.store.one("select id from events where id = ?", (event_id,)):
        raise ValueError(f"événement {event_id} introuvable")
    ctx.store.query("delete from events where id = ?", (event_id,))
    return {"deleted_event_id": event_id}


# ---------------- tâches ----------------
@registry.register("task_add", "Ajoute une tâche (due au format AAAA-MM-JJ, optionnel).", {"title": S, "due": S},
                   "tasks", effect="tasks.write", required=["title"])
def task_add(ctx: ToolContext, title: str, due: str | None = None) -> dict:
    d = date.fromisoformat(due).isoformat() if due else None
    tid = ctx.store.insert("insert into tasks (title, due) values (?, ?)", (title, d))
    return {"task_id": tid, "title": title, "due": d}


@registry.register("task_list", "Liste les tâches.", {"include_done": {"type": "boolean"}}, "tasks")
def task_list(ctx: ToolContext, include_done: bool = False) -> dict:
    sql = "select id, title, due, done from tasks" + ("" if include_done else " where done = ?") + " order by due, id"
    rows = ctx.store.query(sql, () if include_done else (False,))
    return {"tasks": rows}


@registry.register("task_complete", "Marque une tâche comme faite.", {"task_id": I}, "tasks",
                   effect="tasks.write", required=["task_id"])
def task_complete(ctx: ToolContext, task_id: int) -> dict:
    if not ctx.store.one("select id from tasks where id = ?", (task_id,)):
        raise ValueError(f"tâche {task_id} introuvable")
    ctx.store.query("update tasks set done = ? where id = ?", (True, task_id))
    return {"completed_task_id": task_id}


# ---------------- mémoire ----------------
@registry.register("memory_search", "Cherche dans toute la mémoire (faits, conversations, notes). Renvoie des sources citables.",
                   {"query": S}, "memory", required=["query"])
def memory_search(ctx: ToolContext, query: str) -> dict:
    # La conversation en cours est déjà dans le contexte : on cherche ailleurs.
    recent = ctx.store.query("select id from messages where session_id = ? order by id desc limit 16", (ctx.session_id,))
    exclude = {f"msg#{r['id']}" for r in recent}
    return {"results": [m.as_dict() for m in ctx.memory.recall(query, k=8, exclude=exclude)]}


@registry.register("memory_remember", "Mémorise un fait durable que l'utilisateur vient de donner.",
                   {"fact": S}, "memory", effect="memory.write", required=["fact"])
def memory_remember(ctx: ToolContext, fact: str) -> dict:
    return ctx.memory.add_fact(fact, "user", ctx.session_id)


@registry.register("memory_forget", "Oublie un fait par son id (fact#ID).", {"fact_id": I}, "memory",
                   effect="memory.write", required=["fact_id"])
def memory_forget(ctx: ToolContext, fact_id: int) -> dict:
    if not ctx.memory.forget_fact(fact_id):
        raise ValueError(f"fait {fact_id} introuvable")
    return {"forgotten_fact_id": fact_id}


# ---------------- notes ----------------
@registry.register(
    "note_write", "Écrit une note Markdown (ex: people/sarah.md, procedures/backup.md, letters/2026-11.md).",
    {"path": S, "content": S, "mode": {"type": "string", "enum": ["overwrite", "append"]}}, "notes",
    effect="notes.write", required=["path", "content"])
def note_write(ctx: ToolContext, path: str, content: str, mode: str = "overwrite") -> dict:
    return {"path": ctx.memory.write_note(path, content, mode), "mode": mode}


@registry.register("note_read", "Lit une note Markdown.", {"path": S}, "notes", required=["path"])
def note_read(ctx: ToolContext, path: str) -> dict:
    text = ctx.memory.notes.read(path)
    if text is None:
        raise ValueError(f"note {path} introuvable")
    return {"path": path, "content": text}


@registry.register("note_list", "Liste les notes existantes.", {}, "notes")
def note_list(ctx: ToolContext) -> dict:
    return {"notes": ctx.memory.notes.list()}


# ---------------- e-mail ----------------
@registry.register(
    "email_draft", "Prépare un e-mail. Il N'EST PAS envoyé : il attend l'approbation de l'utilisateur dans l'interface.",
    {"to": S, "subject": S, "body": S}, "email", effect="email.draft", required=["to", "subject", "body"])
def email_draft(ctx: ToolContext, to: str, subject: str, body: str) -> dict:
    if "@" not in to:
        raise ValueError("adresse e-mail invalide")
    oid = ctx.store.insert("insert into outbox (turn_id, recipient, subject, body) values (?, ?, ?, ?)",
                           (ctx.turn_id, to, subject, body))
    return {"_status": "pending_approval", "outbox_id": oid, "to": to, "subject": subject,
            "note": "brouillon en attente d'approbation — PAS envoyé"}


def tool_groups() -> set[str]:
    return {t.group for t in registry.tools.values()}
