"""Mode démo (sans clé API) : un faux modèle volontairement menteur.

Sur une demande d'agenda, il répond d'abord « c'est ajouté » SANS appeler d'outil,
exactement le bug qui a motivé le projet. Le vérificateur le rattrape, le force à
appeler l'outil, puis la réponse passe. Pratique pour montrer le mécanisme en entretien.
"""
from __future__ import annotations

import json
import re
import uuid
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

from .config import settings
from .llm import LLMResponse, ScriptedLLM, ToolCall

_TIME = re.compile(r"(\d{1,2})\s*h\s*(\d{2})?")
_DAYS = {"lundi": 0, "mardi": 1, "mercredi": 2, "jeudi": 3, "vendredi": 4, "samedi": 5, "dimanche": 6}


def _when(text: str) -> datetime:
    now = datetime.now(ZoneInfo(settings.timezone))
    t = text.lower()
    day = now + timedelta(days=1)
    if "aujourd" in t:
        day = now
    elif "après-demain" in t:
        day = now + timedelta(days=2)
    else:
        for name, idx in _DAYS.items():
            if name in t:
                day = now + timedelta(days=(idx - now.weekday()) % 7 or 7)
                break
    m = _TIME.search(t)
    h, mi = (int(m.group(1)), int(m.group(2) or 0)) if m else (9, 0)
    return day.replace(hour=h % 24, minute=mi, second=0, microsecond=0)


def _user_text(msg: dict) -> str:
    c = msg.get("content") or ""
    return c.split("\n\n", 1)[-1] if c.startswith("<contexte>") else c


def _demo(messages: list[dict], tools) -> LLMResponse:
    last = messages[-1]
    usage = {"prompt_tokens": sum(len(str(m.get("content", ""))) // 4 for m in messages), "completion_tokens": 40,
             "cached_tokens": 0, "cost": 0.0}
    if last["role"] == "tool":
        env = json.loads(last["content"])
        r = env.get("result") or {}
        if env.get("status") == "error":
            return LLMResponse(f"L'outil a échoué : {r.get('error')}", usage=usage)
        if env.get("status") == "pending_approval":
            return LLMResponse(f"Brouillon prêt pour {r.get('to')} — en attente de ton approbation dans le panneau "
                               "« E-mails ». Il n'est pas envoyé.", usage=usage)
        if "event_id" in r:
            return LLMResponse(f"C'est ajouté à ton agenda : « {r['title']} », {r['start']} (reçu #{env['receipt_id']}).",
                               usage=usage)
        if "results" in r:
            if not r["results"]:
                return LLMResponse("Je ne trouve rien là-dessus dans ma mémoire.", usage=usage)
            best = r["results"][0]
            return LLMResponse(f"D'après [{best['source']}] : {best['content']}", usage=usage)
        if r.get("status") in ("created", "updated"):
            return LLMResponse(f"C'est mémorisé [fact#{r['id']}].", usage=usage)
        return LLMResponse(f"Résultat : {json.dumps(r, ensure_ascii=False)[:300]}", usage=usage)

    text = _user_text(last)
    low = text.lower()
    tool_names = {t["function"]["name"] for t in (tools or [])}

    if last["role"] == "user" and text.startswith("[SYSTÈME — vérificateur]"):
        original = next(_user_text(m) for m in reversed(messages) if m["role"] == "user"
                        and not str(m.get("content", "")).startswith("[SYSTÈME"))
        start = _when(original)
        title = re.sub(r"^(ajoute|mets|programme|planifie)\s+(un|une)?\s*", "", original, flags=re.I)[:60] or "Événement"
        return LLMResponse(None, [ToolCall(uuid.uuid4().hex[:8], "calendar_add_event",
                                           {"title": title, "start": start.strftime("%Y-%m-%dT%H:%M")})], usage)

    if "calendar_add_event" in tool_names and re.search(r"\b(ajoute|mets|programme|planifie|rdv|rendez-vous)\b", low):
        # Le mensonge, volontaire : pas d'outil, mais une confirmation.
        return LLMResponse("Confirmé, l'événement est ajouté à ton agenda.", usage=usage)
    if "email_draft" in tool_names and re.search(r"\b(mail|e-mail)\b", low):
        to = re.search(r"[\w.+-]+@[\w-]+\.[\w.]+", text)
        return LLMResponse(None, [ToolCall(uuid.uuid4().hex[:8], "email_draft", {
            "to": to.group(0) if to else "destinataire@exemple.fr", "subject": "Message d'ATLAS",
            "body": f"Bonjour,\n\n{text}\n\nCordialement"})], usage)
    if "memory_remember" in tool_names and re.search(r"\b(retiens|souviens-toi|remember)\b", low):
        fact = re.sub(r"^.*?(retiens que|retiens|souviens-toi que|remember that)\s*", "", text, flags=re.I)
        return LLMResponse(None, [ToolCall(uuid.uuid4().hex[:8], "memory_remember", {"fact": fact})], usage)
    if "memory_search" in tool_names and "?" in text:
        return LLMResponse(None, [ToolCall(uuid.uuid4().hex[:8], "memory_search", {"query": text})], usage)
    return LLMResponse("Mode démo : branche une clé OpenRouter dans .env pour parler au vrai modèle. "
                       "Essaie « ajoute un rdv demain à 14h avec Karim » pour voir le vérificateur.", usage=usage)


def demo_llm() -> ScriptedLLM:
    return ScriptedLLM(_demo)
