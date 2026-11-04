"""Le vérificateur : aucune réponse ne dit « c'est fait » sans qu'un outil soit d'accord.

1. On découpe la réponse en propositions.
2. On repère les affirmations d'accomplissement (passé composé / participe,
   FR + EN) et l'effet qu'elles supposent (calendar.write, email.send, ...).
3. On les confronte aux reçus du tour (table `actions`).
   - Un e-mail « envoyé » exige un reçu `email.send` au statut `sent` :
     un brouillon en attente ne suffit pas.
   - Un reçu en erreur ne prouve rien.
Les négations, questions et propositions au futur (« je vais ajouter… ») ne sont
pas des affirmations.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field

CAL_OBJ = r"(agenda|calendrier|calendar|[ée]v[ée]nement|event|rdv|rendez-vous|r[ée]union|meeting|cr[ée]neau|entretien)"
TASK_OBJ = r"(t[âa]che|todo|to-do|task|liste de choses)"
MAIL_OBJ = r"(e-?mail|mail|courriel|message|[\w.+-]+@[\w-]+\.\w+)"
NOTE_OBJ = r"(note|notes|fiche|portrait)"

PP = r"(?:é|ée|és|ées|ee)\b"  # participe passé uniquement : « ajouter »/« ajoute » ne sont pas des affirmations


def _v(*stems: str, en: str = "") -> str:
    fr = "|".join(f"{s}{PP}" for s in stems)
    return rf"\b(?:{fr}{'|' + en if en else ''})"


CAL_VERB = _v("ajout", "cr[ée]", "programm", "planifi", "enregistr", "r[ée]serv", "bloqu", "supprim", "annul",
              "d[ée]plac", "d[ée]cal", "confirm",
              en=r"added\b|created\b|scheduled\b|booked\b|blocked\b|deleted\b|cancell?ed\b|moved\b|rescheduled\b")
TASK_VERB = _v("ajout", "cr[ée]", "enregistr", "marqu", "coch", "supprim", "termin",
               en=r"added\b|created\b|marked\b|completed\b|checked\b|ticked\b")
SEND_VERB = _v("envoy", "exp[ée]di", en=r"parti\b|sent\b|delivered\b")
DRAFT_VERB = _v("r[ée]dig", "pr[ée]par", en=r"brouillon\b|draft(?:ed)?\b|prepared\b|written\b")
NOTE_VERB = _v("r[ée]dig", "cr[ée]", "enregistr", "ajout",
               en=r"[ée]crite?s?\b|mise? à jour\b|wrote\b|written\b|saved\b|updated\b|created\b")

# (catégorie, regex, effets acceptés comme preuve, statuts acceptés, ignore le filtre futur)
RULES: list[tuple[str, re.Pattern, set[str], set[str], bool]] = [
    ("email_send", re.compile(rf"{SEND_VERB}.{{0,60}}{MAIL_OBJ}|{MAIL_OBJ}.{{0,60}}{SEND_VERB}", re.I),
     {"email.send"}, {"sent"}, False),
    ("email_draft", re.compile(rf"{DRAFT_VERB}.{{0,60}}{MAIL_OBJ}|{MAIL_OBJ}.{{0,60}}{DRAFT_VERB}", re.I),
     {"email.draft", "email.send"}, {"pending_approval", "sent", "ok"}, False),
    ("calendar", re.compile(rf"{CAL_VERB}.{{0,60}}{CAL_OBJ}|{CAL_OBJ}.{{0,60}}{CAL_VERB}", re.I),
     {"calendar.write"}, {"ok"}, False),
    ("tasks", re.compile(rf"{TASK_VERB}.{{0,60}}{TASK_OBJ}|{TASK_OBJ}.{{0,60}}{TASK_VERB}", re.I),
     {"tasks.write"}, {"ok"}, False),
    ("notes", re.compile(rf"{NOTE_VERB}.{{0,50}}\b{NOTE_OBJ}\b|\b{NOTE_OBJ}\b.{{0,50}}{NOTE_VERB}", re.I),
     {"notes.write", "memory.write"}, {"ok"}, False),
    ("memory", re.compile(
        r"(m[ée]moris(?:é|ée|és|ées)\b|c'est not[ée]|bien not[ée]|je (?:le |l')?retiens|je m'en souviendrai|"
        r"enregistr[ée]e?s? (?:dans|en) (?:ma )?m[ée]moire|gard(?:é|ée) en m[ée]moire|oubli(?:é|ée|és|ées)\b|"
        r"i'?ll remember|remembered|noted|saved (?:it )?to (?:my )?memory|forgotten)", re.I),
     {"memory.write", "notes.write"}, {"ok"}, True),
    ("generic", re.compile(r"^\W*(c'est fait|c'est r[ée]gl[ée]|fait\s*!|voil[àa],? c'est fait|done[.!]|all set|"
                           r"c'est bon,? (?:c'est )?(?:ajout|enregistr|fait))", re.I),
     {"calendar.write", "tasks.write", "memory.write", "notes.write", "email.draft", "email.send"},
     {"ok", "pending_approval", "sent"}, False),
]

NEGATION = re.compile(r"(\bpas\b|\bjamais\b|\baucun(e)?\b|\bnot\b|\bnever\b|n't\b|\bcannot\b|\bunable\b|"
                      r"\bimpossible\b|[ée]chou|\bfailed\b|\bn'(?:a|ai|est|as|avons|ont)\b|\bsans\b|\bno\b)", re.I)
FUTURE = re.compile(r"(\bvais\b|\bva\b|\bvas\b|\ballons\b|\bveux-tu\b|\bvoulez-vous\b|\bsouhaites-tu\b|\bdois-je\b|"
                    r"\bje peux\b|\bpeux-tu\b|\bpourrais\b|\bpourrai\b|\bwould\b|\bshould i\b|\bshall i\b|\bi can\b|"
                    r"\bi will\b|\bi'll\b|\blet me\b|\bwant me\b|\bgoing to\b|\bsi tu veux\b|\bif you\b|"
                    r"\bpr[ée]vu de\b|\bune fois\b|\bd[èe]s que\b|\bquand tu\b)", re.I)
SENTENCE = re.compile(r"(?<=[.!?\n])\s+")
CLAUSE = re.compile(r"\s*(?:[,;:]|\bmais\b|\bbut\b|\bet\b|\band\b)\s*", re.I)


@dataclass
class Claim:
    category: str
    text: str
    accepts: set[str]
    statuses: set[str]

    def as_dict(self) -> dict:
        return {"category": self.category, "text": self.text}


@dataclass
class Verification:
    claims: list[Claim] = field(default_factory=list)
    unsupported: list[Claim] = field(default_factory=list)
    supported_by: dict[str, list[int]] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.unsupported


def _strip_markdown(text: str) -> str:
    text = re.sub(r"```.*?```", " ", text, flags=re.S)       # le code n'affirme rien
    text = re.sub(r"^\s*>.*$", " ", text, flags=re.M)         # ni les citations
    return re.sub(r"[*_`#]", "", text)


def detect_claims(reply: str) -> list[Claim]:
    claims: list[Claim] = []
    seen: set[str] = set()
    for sentence in SENTENCE.split(_strip_markdown(reply)):
        sentence = sentence.strip()
        if not sentence or sentence.endswith("?"):
            continue
        clauses = [c for c in CLAUSE.split(sentence) if c and c.strip()]
        for cat, rx, accepts, statuses, ignore_future in RULES:
            if cat in seen:
                continue
            # L'objet peut être dans la phrase ; le verbe et la négation se jugent par proposition.
            if not rx.search(sentence):
                continue
            hit_clause = None
            for clause in clauses:
                if NEGATION.search(clause):
                    continue
                if not ignore_future and FUTURE.search(clause):
                    continue
                verb_rx = rx if cat in ("memory", "generic") else None
                if verb_rx is not None and not verb_rx.search(clause):
                    continue
                if verb_rx is None and not _clause_has_verb(cat, clause):
                    continue
                hit_clause = clause
                break
            if hit_clause and not (cat == "generic" and claims):
                claims.append(Claim(cat, sentence[:200], accepts, statuses))
                seen.add(cat)
                if cat == "email_send":
                    seen.add("email_draft")
    return claims


_VERBS = {"calendar": CAL_VERB, "tasks": TASK_VERB, "email_send": SEND_VERB, "email_draft": DRAFT_VERB,
          "notes": NOTE_VERB}


def _clause_has_verb(cat: str, clause: str) -> bool:
    return bool(re.search(_VERBS[cat], clause, re.I))


def verify(reply: str, receipts: list[dict]) -> Verification:
    result = Verification(claims=detect_claims(reply))
    for claim in result.claims:
        proof = [r["action_id"] for r in receipts
                 if r.get("effect") in claim.accepts and r.get("status") in claim.statuses]
        if proof:
            result.supported_by[claim.category] = proof
        else:
            result.unsupported.append(claim)
    return result


LABELS_FR = {
    "calendar": "une modification de l'agenda", "tasks": "une modification des tâches",
    "email_send": "l'envoi d'un e-mail", "email_draft": "la préparation d'un e-mail",
    "notes": "l'écriture d'une note", "memory": "un enregistrement en mémoire", "generic": "une action terminée",
}


def correction_prompt(v: Verification) -> str:
    lines = [f"- tu affirmes {LABELS_FR[c.category]} : « {c.text} »" for c in v.unsupported]
    return (
        "VÉRIFICATION ÉCHOUÉE. Aucun reçu d'outil réussi ne correspond à ces affirmations pendant ce tour :\n"
        + "\n".join(lines)
        + "\nSoit tu appelles maintenant l'outil qui réalise réellement l'action, soit tu réécris ta réponse "
          "sans prétendre que c'est fait. Un e-mail n'est JAMAIS envoyé par toi : il attend l'approbation de "
          "l'utilisateur. N'invente aucun reçu."
    )


def blocked_reply(v: Verification) -> str:
    what = ", ".join(LABELS_FR[c.category] for c in v.unsupported)
    return (f"⚠️ Je ne peux pas confirmer {what} : aucun outil ne l'a exécuté pendant cet échange, "
            "donc rien n'a été modifié. Reformule ou demande-moi de réessayer.")
