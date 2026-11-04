"""Couche de stockage : Postgres/Supabase en prod, SQLite en local et en test.

Le même SQL tourne sur les deux (placeholders `?` convertis pour psycopg).
"""
from __future__ import annotations

import json
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

import numpy as np

from .config import settings

SQLITE_SCHEMA = """
create table if not exists sessions (id text primary key, title text, created_at text default current_timestamp);
create table if not exists messages (id integer primary key autoincrement, session_id text not null, turn_id text not null,
  role text not null, content text not null, created_at text default current_timestamp);
create index if not exists messages_session_idx on messages(session_id, id);
create table if not exists actions (id integer primary key autoincrement, session_id text not null, turn_id text not null,
  tool text not null, effect text, args text not null default '{}', result text, status text not null,
  created_at text default current_timestamp);
create index if not exists actions_turn_idx on actions(turn_id);
create table if not exists verifications (id integer primary key autoincrement, turn_id text not null, claims text not null default '[]',
  unsupported text not null default '[]', verdict text not null, created_at text default current_timestamp);
create table if not exists turns (id text primary key, session_id text not null, effort text, model text,
  prompt_tokens integer default 0, completion_tokens integer default 0, cached_tokens integer default 0,
  cost_usd real default 0, created_at text default current_timestamp);
create table if not exists facts (id integer primary key autoincrement, content text not null, speaker text not null,
  session_id text, message_id integer, created_at text default current_timestamp, updated_at text default current_timestamp);
create table if not exists chunks (id integer primary key autoincrement, kind text not null, ref text not null,
  content text not null, embedding text not null, created_at text default current_timestamp, unique(kind, ref));
create table if not exists events (id integer primary key autoincrement, title text not null, starts_at text not null,
  ends_at text, created_at text default current_timestamp);
create table if not exists tasks (id integer primary key autoincrement, title text not null, due text,
  done integer not null default 0, created_at text default current_timestamp);
create table if not exists outbox (id integer primary key autoincrement, turn_id text, recipient text not null,
  subject text not null, body text not null, status text not null default 'pending_approval',
  created_at text default current_timestamp, decided_at text);
"""

JSON_COLS = {"args", "result", "claims", "unsupported"}


def _norm(row: dict) -> dict:
    out = {}
    for k, v in row.items():
        if isinstance(v, datetime):
            v = v.isoformat()
        elif k in JSON_COLS and isinstance(v, str):
            try:
                v = json.loads(v)
            except json.JSONDecodeError:
                pass
        elif k == "done" and v is not None:
            v = bool(v)
        elif hasattr(v, "is_finite"):  # Decimal
            v = float(v)
        out[k] = v
    return out


class Store:
    def __init__(self, sqlite_path: str | None = None, database_url: str | None = None):
        self.lock = threading.RLock()
        url = database_url if database_url is not None else settings.database_url
        self.pg = bool(url) and url.startswith(("postgres://", "postgresql://"))
        if self.pg:
            import psycopg
            from psycopg.rows import dict_row

            self.conn = psycopg.connect(url, autocommit=True, row_factory=dict_row)
        else:
            path = sqlite_path or settings.sqlite_path
            if path != ":memory:":
                Path(path).parent.mkdir(parents=True, exist_ok=True)
            self.conn = sqlite3.connect(path, check_same_thread=False)
            self.conn.row_factory = sqlite3.Row
            self.conn.executescript(SQLITE_SCHEMA)
            self.conn.commit()

    # ---------- primitives ----------
    def _sql(self, sql: str) -> str:
        return sql.replace("?", "%s") if self.pg else sql

    def jsonb(self) -> str:
        return "?::jsonb" if self.pg else "?"

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[dict]:
        with self.lock:
            cur = self.conn.execute(self._sql(sql), tuple(params))
            rows = cur.fetchall() if cur.description else []
            if not self.pg:
                self.conn.commit()
            return [_norm(dict(r)) for r in rows]

    def one(self, sql: str, params: Iterable[Any] = ()) -> dict | None:
        rows = self.query(sql, params)
        return rows[0] if rows else None

    def insert(self, sql: str, params: Iterable[Any] = ()) -> int:
        row = self.one(sql + " returning id", params)
        return row["id"]

    # ---------- registre exact (couche 2) ----------
    def ensure_session(self, session_id: str, title: str | None = None) -> None:
        if not self.one("select id from sessions where id = ?", (session_id,)):
            self.query("insert into sessions (id, title) values (?, ?)", (session_id, title))

    def add_message(self, session_id: str, turn_id: str, role: str, content: str) -> int:
        return self.insert(
            "insert into messages (session_id, turn_id, role, content) values (?, ?, ?, ?)",
            (session_id, turn_id, role, content),
        )

    def history(self, session_id: str, limit_messages: int) -> list[dict]:
        rows = self.query(
            "select id, role, content from messages where session_id = ? order by id desc limit ?",
            (session_id, limit_messages),
        )
        return list(reversed(rows))

    def log_action(self, session_id: str, turn_id: str, tool: str, effect: str | None,
                   args: dict, result: Any, status: str) -> int:
        j = self.jsonb()
        return self.insert(
            f"insert into actions (session_id, turn_id, tool, effect, args, result, status) values (?, ?, ?, ?, {j}, {j}, ?)",
            (session_id, turn_id, tool, effect, json.dumps(args, ensure_ascii=False),
             json.dumps(result, ensure_ascii=False, default=str), status),
        )

    def actions_for_turn(self, turn_id: str) -> list[dict]:
        return self.query("select * from actions where turn_id = ? order by id", (turn_id,))

    def log_verification(self, turn_id: str, claims: list, unsupported: list, verdict: str) -> None:
        j = self.jsonb()
        self.query(
            f"insert into verifications (turn_id, claims, unsupported, verdict) values (?, {j}, {j}, ?)",
            (turn_id, json.dumps(claims), json.dumps(unsupported), verdict),
        )

    def log_turn(self, turn_id: str, session_id: str, effort: str, model: str, usage: dict) -> None:
        self.query(
            "insert into turns (id, session_id, effort, model, prompt_tokens, completion_tokens, cached_tokens, cost_usd)"
            " values (?, ?, ?, ?, ?, ?, ?, ?)",
            (turn_id, session_id, effort, model, usage.get("prompt_tokens", 0), usage.get("completion_tokens", 0),
             usage.get("cached_tokens", 0), usage.get("cost", 0.0)),
        )

    # ---------- index vectoriel (couche 1) ----------
    def upsert_chunk(self, kind: str, ref: str, content: str, embedding: np.ndarray) -> None:
        vec = "[" + ",".join(f"{x:.6f}" for x in embedding.tolist()) + "]"
        cast = "?::vector" if self.pg else "?"
        self.query(
            f"insert into chunks (kind, ref, content, embedding) values (?, ?, ?, {cast}) "
            "on conflict (kind, ref) do update set content = excluded.content, embedding = excluded.embedding",
            (kind, ref, content, vec),
        )

    def delete_chunk(self, kind: str, ref: str) -> None:
        self.query("delete from chunks where kind = ? and ref = ?", (kind, ref))

    def vector_search(self, embedding: np.ndarray, k: int = 12) -> list[dict]:
        if self.pg:
            vec = "[" + ",".join(f"{x:.6f}" for x in embedding.tolist()) + "]"
            return self.query("select * from match_chunks(?::vector, ?)", (vec, k))
        rows = self.query("select kind, ref, content, embedding, created_at from chunks")
        if not rows:
            return []
        mat = np.array([json.loads(r["embedding"]) for r in rows], dtype=np.float32)
        sims = mat @ embedding.astype(np.float32)
        order = np.argsort(-sims)[:k]
        out = []
        for i in order:
            r = dict(rows[i])
            r.pop("embedding")
            r["similarity"] = float(sims[i])
            out.append(r)
        return out
