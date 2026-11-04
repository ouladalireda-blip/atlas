"""La boucle d'ATLAS : trier -> rappeler -> raisonner + outils -> vérifier -> mémoriser."""
from __future__ import annotations

import uuid
from datetime import datetime
from zoneinfo import ZoneInfo

from .config import settings
from .db import Store
from .llm import LLMError, LLMResponse
from .memory.core import MemorySystem
from .tools.builtin import registry
from .tools.registry import ToolContext, receipt_for_model
from .triage import Triager
from .verifier import blocked_reply, correction_prompt, verify

# Préfixe STABLE, octet pour octet identique à chaque appel => cache de prompt efficace.
# Tout ce qui change (date, souvenirs) va à la fin, dans le message utilisateur.
SYSTEM_PROMPT = """Tu es ATLAS, l'assistant personnel de ton utilisateur. Tu réponds en français, de façon directe et concise.

RÈGLES NON NÉGOCIABLES
1. Tu ne dis jamais qu'une action est faite (ajoutée, créée, envoyée, notée, mémorisée…) sans avoir reçu,
   PENDANT CE TOUR, un reçu d'outil au statut "ok". Chaque réponse est vérifiée contre les reçus réels ;
   une affirmation sans reçu est bloquée.
2. Tu n'envoies jamais d'e-mail. `email_draft` crée un brouillon qui attend l'approbation de l'utilisateur :
   dis-le comme ça.
3. Quand tu utilises un souvenir du bloc <memoire>, cite sa source entre crochets, ex : [fact#12], [msg#40],
   [note:people/karim.md]. Si la mémoire ne contient pas la réponse, dis-le ; n'invente pas.
4. Si un outil renvoie une erreur, dis ce qui a échoué. Ne prétends pas l'inverse.
5. Les dates relatives (« demain », « jeudi ») se calculent à partir de la date fournie dans <contexte>.
6. Les notes Markdown sont ta mémoire de travail : portraits dans people/, procédures dans procedures/,
   lettres à ton futur toi dans letters/.
"""


class Agent:
    def __init__(self, store: Store, llm, memory: MemorySystem | None = None, triager: Triager | None = None):
        self.store = store
        self.llm = llm
        self.memory = memory or MemorySystem(store)
        self.triage = triager or Triager()

    def _context_block(self, memories) -> str:
        now = datetime.now(ZoneInfo(settings.timezone))
        ctx = f"<contexte>maintenant : {now.strftime('%A %d/%m/%Y %H:%M')} ({settings.timezone})</contexte>"
        if memories:
            lines = "\n".join(f"[{m.source}] {m.content[:400]}" for m in memories)
            ctx += f"\n<memoire>\n{lines}\n</memoire>"
        return ctx

    def run(self, session_id: str, text: str) -> dict:
        turn_id = uuid.uuid4().hex[:12]
        self.store.ensure_session(session_id, text[:60])
        history = self.store.history(session_id, settings.history_turns * 2)
        tri = self.triage(text)
        in_context = {f"msg#{m['id']}" for m in history}
        memories = self.memory.recall(text, exclude=in_context) if tri.recall else []

        user_msg_id = self.store.add_message(session_id, turn_id, "user", text)
        self.memory.index_message(user_msg_id, "user", text)
        tools = registry.schemas(set(tri.groups)) if tri.effort != "light" else registry.schemas(set())

        messages: list[dict] = [{"role": "system", "content": SYSTEM_PROMPT}]
        messages += [{"role": m["role"], "content": m["content"]} for m in history]
        messages.append({"role": "user", "content": f"{self._context_block(memories)}\n\n{text}"})

        ctx = ToolContext(self.store, self.memory, session_id, turn_id, settings.timezone)
        receipts: list[dict] = []
        usage = {"prompt_tokens": 0, "completion_tokens": 0, "cached_tokens": 0, "cost": 0.0}
        corrections = 0
        verdict = "no_claims"
        reply = ""
        model_used = tri.model

        try:
            rounds = 0
            while True:
                resp: LLMResponse = self.llm.chat(model=tri.model, messages=messages, tools=tools or None)
                model_used = resp.model or tri.model
                for k in usage:
                    usage[k] += resp.usage.get(k, 0) or 0
                if resp.tool_calls and rounds < settings.max_tool_rounds:
                    rounds += 1
                    messages.append(resp.as_message())
                    for tc in resp.tool_calls:
                        receipt = registry.run(ctx, tc.name, tc.arguments)
                        receipts.append(receipt)
                        messages.append({"role": "tool", "tool_call_id": tc.id, "content": receipt_for_model(receipt)})
                    continue

                reply = (resp.content or "").strip()
                check = verify(reply, receipts)
                if check.ok:
                    verdict = "corrected" if corrections else ("verified" if check.claims else "no_claims")
                    break
                if corrections == 0:
                    corrections += 1
                    self.store.log_verification(turn_id, [c.as_dict() for c in check.claims],
                                                [c.as_dict() for c in check.unsupported], "rejected_draft")
                    messages.append({"role": "assistant", "content": reply})
                    messages.append({"role": "user", "content": f"[SYSTÈME — vérificateur]\n{correction_prompt(check)}"})
                    continue
                reply = blocked_reply(check)
                verdict = "blocked"
                break
        except LLMError as e:
            reply, verdict = f"⚠️ Le modèle est indisponible : {e}", "error"
            check = verify("", receipts)

        self.store.log_verification(turn_id, [c.as_dict() for c in check.claims],
                                    [c.as_dict() for c in check.unsupported], verdict)
        asst_id = self.store.add_message(session_id, turn_id, "assistant", reply)
        self.memory.index_message(asst_id, "assistant", reply)
        self.store.log_turn(turn_id, session_id, tri.effort, model_used, usage)

        return {
            "turn_id": turn_id,
            "session_id": session_id,
            "reply": reply,
            "triage": tri.as_dict(),
            "recall": [m.as_dict() for m in memories],
            "receipts": [{k: r[k] for k in ("action_id", "tool", "effect", "status", "result")} for r in receipts],
            "verification": {
                "verdict": verdict,
                "claims": [c.as_dict() for c in check.claims],
                "unsupported": [c.as_dict() for c in check.unsupported],
                "supported_by": check.supported_by,
            },
            "usage": usage,
            "model": model_used,
            "_post": {"extract": tri.extract_facts and verdict != "error", "user": text, "assistant": reply,
                      "message_id": user_msg_id, "model": settings.model_fast},
        }

    def post_turn(self, result: dict) -> list[dict]:
        """Après la réponse (en tâche de fond) : extraire les faits durables, étiquetés par locuteur."""
        p = result.get("_post") or {}
        if not p.get("extract"):
            return []
        try:
            return self.memory.extract_facts(self.llm, p["model"], p["user"], p["assistant"],
                                             result["session_id"], p["message_id"])
        except LLMError:
            return []
