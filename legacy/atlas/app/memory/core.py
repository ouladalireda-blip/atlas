"""Mémoire en quatre couches, une seule étape de rappel.

1. Vecteurs      -> `chunks` : retrouver par le sens (messages, faits, notes)
2. Registre exact -> `messages` / `actions` : la trace mot pour mot
3. Faits durables -> `facts` : extraits des conversations, étiquetés user/assistant
4. Notes Markdown -> data/notes/ : écrites et relues par l'assistant lui-même

`recall()` interroge les couches 1, 2 et (via l'index) 3-4, reranke, et renvoie
chaque résultat avec une source citable : fact#12, msg#44, note:people/sarah.md.
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..config import settings
from ..db import Store
from .embeddings import get_embedder, tokens

KIND_BONUS = {"fact": 0.06, "note": 0.05, "message": 0.0}
DEDUP_THRESHOLD = 0.88


@dataclass
class Memory:
    source: str
    kind: str
    content: str
    score: float

    def as_dict(self) -> dict:
        return {"source": self.source, "kind": self.kind, "content": self.content, "score": round(self.score, 3)}


def _age_days(created_at: str | None) -> float:
    if not created_at:
        return 0.0
    try:
        dt = datetime.fromisoformat(str(created_at).replace(" ", "T").replace("Z", "+00:00"))
    except ValueError:
        return 0.0
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return max(0.0, (datetime.now(timezone.utc) - dt).total_seconds() / 86400)


class NotesStore:
    """Couche 4 : un dossier de Markdown. Lettres au futur soi, procédures, portraits."""

    def __init__(self, root: str):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)

    def _path(self, rel: str) -> Path:
        rel = rel.strip().lstrip("/")
        if not rel.endswith(".md"):
            rel += ".md"
        p = (self.root / rel).resolve()
        if self.root not in p.parents and p != self.root:
            raise ValueError("chemin de note hors du dossier notes")
        return p

    def rel(self, p: Path) -> str:
        return p.relative_to(self.root).as_posix()

    def write(self, rel: str, content: str, mode: str = "overwrite") -> str:
        p = self._path(rel)
        p.parent.mkdir(parents=True, exist_ok=True)
        if mode == "append" and p.exists():
            content = p.read_text(encoding="utf-8").rstrip() + "\n\n" + content
        p.write_text(content, encoding="utf-8")
        return self.rel(p)

    def read(self, rel: str) -> str | None:
        p = self._path(rel)
        return p.read_text(encoding="utf-8") if p.exists() else None

    def list(self) -> list[str]:
        return sorted(self.rel(p) for p in self.root.rglob("*.md"))


FACT_PROMPT = """Extrais de cet échange les faits DURABLES sur l'utilisateur ou son monde
(personnes, préférences stables, projets, décisions). Ignore le bavardage et l'éphémère.
Chaque fait : une phrase courte, autonome, à la 3e personne ("L'utilisateur ...").
speaker = "user" si l'utilisateur l'a dit, "assistant" si seul l'assistant l'a affirmé.
Réponds UNIQUEMENT en JSON : {"facts": [{"fact": "...", "speaker": "user"}]}  (liste vide si rien)

UTILISATEUR: {user}
ASSISTANT: {assistant}"""


class MemorySystem:
    def __init__(self, store: Store, notes_dir: str | None = None, embedder=None):
        self.store = store
        self.notes = NotesStore(notes_dir or settings.notes_dir)
        self.embedder = embedder or get_embedder()

    # ---------- écriture ----------
    def index_message(self, message_id: int, role: str, content: str) -> None:
        if len(content.strip()) < 3:
            return
        self.store.upsert_chunk("message", str(message_id), f"{role}: {content}", self.embedder.embed(content))

    def add_fact(self, content: str, speaker: str, session_id: str | None = None,
                 message_id: int | None = None) -> dict:
        """Ajoute un fait, ou met à jour le plus proche s'il dit la même chose (dédoublonnage à la mem0)."""
        emb = self.embedder.embed(content)
        for hit in self.store.vector_search(emb, k=3):
            if hit["kind"] == "fact" and hit["similarity"] >= DEDUP_THRESHOLD:
                fid = int(hit["ref"])
                self.store.query(
                    "update facts set content = ?, speaker = ?, updated_at = current_timestamp where id = ?",
                    (content, speaker, fid),
                )
                self.store.upsert_chunk("fact", str(fid), content, emb)
                return {"id": fid, "status": "updated"}
        fid = self.store.insert(
            "insert into facts (content, speaker, session_id, message_id) values (?, ?, ?, ?)",
            (content, speaker, session_id, message_id),
        )
        self.store.upsert_chunk("fact", str(fid), content, emb)
        return {"id": fid, "status": "created"}

    def forget_fact(self, fact_id: int) -> bool:
        if not self.store.one("select id from facts where id = ?", (fact_id,)):
            return False
        self.store.query("delete from facts where id = ?", (fact_id,))
        self.store.delete_chunk("fact", str(fact_id))
        return True

    def write_note(self, rel: str, content: str, mode: str = "overwrite") -> str:
        path = self.notes.write(rel, content, mode)
        full = self.notes.read(path) or ""
        self.store.upsert_chunk("note", path, f"[{path}]\n{full[:2000]}", self.embedder.embed(path + " " + full[:2000]))
        return path

    def reindex_notes(self) -> int:
        n = 0
        for rel in self.notes.list():
            full = self.notes.read(rel) or ""
            self.store.upsert_chunk("note", rel, f"[{rel}]\n{full[:2000]}", self.embedder.embed(rel + " " + full[:2000]))
            n += 1
        return n

    def extract_facts(self, llm, model: str, user_msg: str, assistant_msg: str,
                      session_id: str, message_id: int | None) -> list[dict]:
        prompt = FACT_PROMPT.replace("{user}", user_msg).replace("{assistant}", assistant_msg)
        resp = llm.chat(model=model, messages=[{"role": "user", "content": prompt}], json_mode=True)
        raw = resp.content or ""
        m = re.search(r"\{.*\}", raw, re.S)
        try:
            items = json.loads(m.group(0)).get("facts", []) if m else []
        except json.JSONDecodeError:
            items = []
        saved = []
        for it in items[:5]:
            fact = str(it.get("fact", "")).strip()
            speaker = it.get("speaker") if it.get("speaker") in ("user", "assistant") else "assistant"
            if len(fact) > 8:
                saved.append({**self.add_fact(fact, speaker, session_id, message_id), "fact": fact})
        return saved

    # ---------- rappel ----------
    def recall(self, query: str, k: int = 6, min_score: float = 0.18,
               exclude: set[str] | None = None) -> list[Memory]:
        """`exclude` : sources déjà présentes dans le contexte (historique récent) — inutile de les rappeler."""
        exclude = exclude or set()
        q_tokens = set(tokens(query))
        candidates: dict[str, dict] = {}

        # Couche 1 (+3 et 4 indexées) : par le sens
        for hit in self.store.vector_search(self.embedder.embed(query), k=k * 3):
            candidates[f"{hit['kind']}:{hit['ref']}"] = hit

        # Couche 2 : correspondance exacte sur les mots rares (noms propres, chiffres…)
        rare = sorted(q_tokens, key=len, reverse=True)[:2]
        for word in rare:
            if len(word) < 4:
                continue
            for row in self.store.query(
                "select id, role, content, created_at from messages where lower(content) like ? order by id desc limit 5",
                (f"%{word}%",),
            ):
                key = f"message:{row['id']}"
                candidates.setdefault(key, {"kind": "message", "ref": str(row["id"]),
                                            "content": f"{row['role']}: {row['content']}",
                                            "similarity": 0.0, "created_at": row["created_at"]})

        # Rerank : sens + recouvrement lexical + récence + type
        out: list[Memory] = []
        for hit in candidates.values():
            c_tokens = set(tokens(hit["content"]))
            lexical = len(q_tokens & c_tokens) / len(q_tokens) if q_tokens else 0.0
            recency = math.exp(-_age_days(hit.get("created_at")) / 30)
            score = 0.55 * hit["similarity"] + 0.30 * lexical + 0.09 * recency + KIND_BONUS.get(hit["kind"], 0)
            if score < min_score:
                continue
            src = {"fact": f"fact#{hit['ref']}", "message": f"msg#{hit['ref']}", "note": f"note:{hit['ref']}"}[hit["kind"]]
            if src in exclude:
                continue
            out.append(Memory(src, hit["kind"], hit["content"], score))
        out.sort(key=lambda m: m.score, reverse=True)
        return out[:k]
