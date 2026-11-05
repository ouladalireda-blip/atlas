"""Vérification des affirmations d'action d'une réponse à partir des reçus d'outils.

Le cœur connaît les verbes (`core/lexicon.py`), les compétences connaissent leurs objets (les
noms de leur `Domain`). L'effet d'une affirmation est la classe du verbe (write, delete, send)
appliquée au domaine du nom le plus proche. Les listes de ce module ne contiennent que de la
grammaire française et anglaise.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from atlas.core.lexicon import (
    PARTICIPLE_CLASS,
    PRESENT_EN_CLASS,
    PRESENT_FR_CLASS,
    PROGRESSIVE_EN_CLASS,
    Domain,
)
from atlas.core.receipts import Receipt, ReceiptStatus

type Domains = Mapping[str, Domain]

# Écart maximal, en mots, entre un verbe d'action et le nom d'un domaine.
NEAR_DISTANCE = 6

_CODE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.DOTALL)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+|\n+")
_CLAUSE_BREAK = re.compile(r"[,;:()]|\s[-–—]\s|\b(?:mais|donc|car|but|so)\b", re.IGNORECASE)
_COORDINATION = re.compile(r"\b(?:et|puis|and|then)\b", re.IGNORECASE)
_WORD = re.compile(r"\w+")
_GENERIC_DONE = re.compile(r"\bc est (?:bien |déjà )?fait\b|\bdone\b")
_SENTENCE_CLOSERS = " \t!*_\"'»)"

# Un participe placé juste après un déterminant est un nom : « une mise à jour », « the set ».
# Le français « a » n'y figure pas : c'est aussi l'auxiliaire de « a ajouté ».
_DETERMINERS = frozenset(
    "le la les l un une des du de d au aux ma ta sa mon ton son mes tes ses notre votre nos "
    "vos leur leurs ce cet cette ces the an your my this that these those our their his her".split()
)
# Ces marqueurs annulent tout ce qui les suit dans la proposition, mais pas ce qui les précède :
# « ajouté sans problème » reste une affirmation.
_NEGATIONS = frozenset(
    "ne n pas jamais aucun aucune rien ni non sans impossible échec "
    "not no never none nor cannot without unable".split()
)
_UNREALIZED = frozenset(
    "vais vas va allons allez vont serai seras sera serons serez seront serait seraient "
    "peux peut pouvons pouvez peuvent pourrai pourrais pourrait dois doit devons devez doivent "
    "devrait veux veut voulez être soit soient faut faudrait "
    "ll will shall going can could would should may might must be".split()
)
_BLOCKERS = _NEGATIONS | _UNREALIZED
# « ne ... que » restreint au lieu de nier, sauf si un autre mot de négation s'intercale.
_NE = frozenset({"ne", "n"})
_RESTRICTION_WORD = frozenset({"que", "qu"})
_OTHER_NEGATIONS = frozenset(
    "pas jamais aucun aucune rien ni non sans plus personne guère point not no never".split()
)

# Présent de l'indicatif à la 1re personne : « je programme », « i send », « i'm scheduling ».
_FRENCH_SUBJECTS = frozenset({"je", "j"})
_ENGLISH_SUBJECT = "i"
_CLITICS = frozenset("l le la les te t vous m me y en bien déjà aussi vraiment maintenant".split())
_ENGLISH_ADVERBS = frozenset("just now also already really".split())
# Une proposition subordonnée (« que je programme ») n'est pas une affirmation.
_SUBORDINATORS = frozenset({"que", "qu"})

# « je (peux) (te|vous) confirme(r) que », « je t'assure que », « i can confirm » : l'auxiliaire
# n'exprime pas ici une possibilité, c'est une affirmation.
_ASSERTION_VERBS_FR = frozenset({"confirme", "confirmer", "assure", "assurer"})
_ASSERTION_VERBS_EN = frozenset({"confirm", "assure"})
_ADDRESSEES_FR = frozenset({"te", "t", "vous"})
_CONDITIONS = (
    ("dès", "que"),
    ("dès", "qu"),
    ("quand",),
    ("si",),
    ("une", "fois", "que"),
    ("une", "fois", "qu"),
    ("once",),
    ("when",),
    ("if",),
)
_QUESTION_OPENERS = frozenset(
    "est veux voulez souhaites souhaitez as avez dois faut quel quelle quels quelles comment "
    "pourquoi quand où qui que do does did is are was were have has should shall can could "
    "would will what which when where why how who".split()
)


@dataclass(frozen=True)
class Claim:
    """Affirmation d'action ; effect vaut None si n'importe quelle écriture la prouve.

    declared vaut False quand le domaine du nom ne déclare pas la classe du verbe : l'effet est
    alors « <domaine>.<classe> » et aucun reçu ne peut le prouver.

    any_of est rempli pour une affirmation générique de classe, sans objet nommé et plusieurs
    domaines possibles (« c'est ajouté ») : elle est prouvée par un reçu réussi de n'importe lequel
    de ces effets. Son effect vaut alors « *.<classe> ».
    """

    effect: str | None
    text: str
    declared: bool = True
    any_of: tuple[str, ...] = ()


@dataclass(frozen=True)
class Verification:
    """Bilan d'une réponse : affirmations trouvées, non prouvées et preuves par affirmation."""

    claims: list[Claim]
    unsupported: list[Claim]
    supported_by: dict[Claim, list[Receipt]]


def _tokens(text: str) -> list[str]:
    """Découpe un texte en mots normalisés ; n't devient not pour rester une négation."""
    normalized = unicodedata.normalize("NFKC", text).lower().replace("’", "'")
    return _WORD.findall(normalized.replace("n't", " not "))


def _participle_class(tokens: Sequence[str], index: int) -> str | None:
    """Retourne la classe du participe passé à cette position, ou None s'il n'y en a pas."""
    verb_class = PARTICIPLE_CLASS.get(tokens[index])
    if verb_class is None or (index > 0 and tokens[index - 1] in _DETERMINERS):
        return None
    return verb_class


def _starts_condition(tokens: Sequence[str], index: int) -> bool:
    """Indique si une condition commence à cette position."""
    return any(
        tuple(tokens[index : index + len(condition)]) == condition for condition in _CONDITIONS
    )


def _split_propositions(sentence: str) -> list[str]:
    """Découpe une phrase en propositions.

    Un participe coordonné sans sujet reste avec ce qui précède, pour hériter de sa négation :
    « je n'ai pas créé l'événement et envoyé le mail ».
    """
    propositions: list[str] = []
    for clause in _CLAUSE_BREAK.split(sentence):
        first, *others = _COORDINATION.split(clause)
        current = first
        for part in others:
            tokens = _tokens(part)
            if tokens and _participle_class(tokens, 0) is not None:
                current = f"{current} et {part}"
            else:
                propositions.append(current)
                current = part
        propositions.append(current)
    return [proposition.strip() for proposition in propositions if _tokens(proposition)]


def _stop_after_condition(propositions: list[str]) -> list[str]:
    """Retire ce qui suit une condition : « dès que tu valides, l'événement est ajouté »."""
    for position, proposition in enumerate(propositions):
        tokens = _tokens(proposition)
        if any(_starts_condition(tokens, index) for index in range(len(tokens))):
            return propositions[: position + 1]
    return propositions


def _affirmative_propositions(reply: str) -> list[str]:
    """Retourne les propositions de la réponse, hors code et hors questions.

    Dans une phrase interrogative, seule la dernière proposition porte le point d'interrogation,
    sauf si la phrase s'ouvre par un mot de question : « c'est ajouté, veux-tu un rappel ? »
    garde donc son affirmation.
    """
    propositions: list[str] = []
    for sentence in _SENTENCE_BREAK.split(_CODE.sub(" ", reply)):
        parts = _split_propositions(sentence)
        if sentence.rstrip(_SENTENCE_CLOSERS).endswith("?"):
            opens_as_question = bool(parts) and _tokens(parts[0])[0] in _QUESTION_OPENERS
            parts = [] if opens_as_question else parts[:-1]
        propositions.extend(_stop_after_condition(parts))
    return propositions


def _matches_at(tokens: Sequence[str], start: int, word: Sequence[str]) -> bool:
    """Compare un nom du domaine aux mots du texte, en acceptant le pluriel."""
    chunk = tokens[start : start + len(word)]
    return len(chunk) == len(word) and all(
        token in (expected, f"{expected}s", f"{expected}x")
        for token, expected in zip(chunk, word, strict=True)
    )


def _present_verb_after(tokens: Sequence[str], subject: int) -> tuple[int, str] | None:
    """Retourne (position, classe) du verbe d'action au présent qui suit « je » ou « i »."""
    if subject > 0 and tokens[subject - 1] in _SUBORDINATORS:
        return None
    position = subject + 1
    if tokens[subject] in _FRENCH_SUBJECTS:
        while position < len(tokens) and tokens[position] in _CLITICS:
            position += 1
        table = PRESENT_FR_CLASS
    else:
        while position < len(tokens) and tokens[position] in _ENGLISH_ADVERBS:
            position += 1
        table = PRESENT_EN_CLASS
        if position < len(tokens) and tokens[position] in {"m", "am"}:
            # « i'm scheduling » : forme progressive.
            position += 1
            table = PROGRESSIVE_EN_CLASS
    verb_class = table.get(tokens[position]) if position < len(tokens) else None
    return None if verb_class is None else (position, verb_class)


def _present_actions(tokens: Sequence[str]) -> dict[int, str]:
    """Retourne position -> classe des verbes d'action conjugués au présent à la 1re personne."""
    found: dict[int, str] = {}
    for index, token in enumerate(tokens):
        if token in _FRENCH_SUBJECTS or token == _ENGLISH_SUBJECT:
            match = _present_verb_after(tokens, index)
            if match is not None:
                found[match[0]] = match[1]
    return found


def _assertion_formulas(tokens: Sequence[str]) -> tuple[set[int], set[int]]:
    """Repère les formules « je (peux) te confirme que », « je t'assure que », « i can confirm ».

    Retourne (positions de « peux »/« can », positions du verbe de la formule). L'auxiliaire n'y
    est pas une possibilité mais une affirmation ; le verbe (« confirme ») dit ce que l'assistant
    déclare, pas une action faite sur l'objet, donc il ne compte pas comme verbe d'action.
    """
    auxiliaries: set[int] = set()
    verbs: set[int] = set()
    for start, token in enumerate(tokens):
        position = start + 1
        auxiliary = None
        if token == "je":
            if tokens[position : position + 1] == ["peux"]:
                auxiliary, position = position, position + 1
            if tokens[position : position + 1] and tokens[position] in _ADDRESSEES_FR:
                position += 1
            verb = tokens[position : position + 1]
            follows = tokens[position + 1 : position + 2]
            if verb and verb[0] in _ASSERTION_VERBS_FR and follows and follows[0] in _SUBORDINATORS:
                verbs.add(position)
                if auxiliary is not None:
                    auxiliaries.add(auxiliary)
        elif token == _ENGLISH_SUBJECT:
            if tokens[position : position + 1] == ["can"]:
                auxiliary, position = position, position + 1
            verb = tokens[position : position + 1]
            that = tokens[position + 1 : position + 2] == ["that"]
            if verb and verb[0] in _ASSERTION_VERBS_EN and (auxiliary is not None or that):
                verbs.add(position)
                if auxiliary is not None:
                    auxiliaries.add(auxiliary)
    return auxiliaries, verbs


def _is_restriction(tokens: Sequence[str], index: int) -> bool:
    """Indique si ce « ne » ouvre une restriction « ne ... que » et non une négation."""
    if tokens[index] not in _NE:
        return False
    for token in tokens[index + 1 :]:
        if token in _RESTRICTION_WORD:
            return True
        if token in _OTHER_NEGATIONS:
            return False
    return False


def _claim_limit(tokens: Sequence[str]) -> int:
    """Retourne la position à partir de laquelle plus rien n'est affirmé (marqueur bloquant)."""
    exempt, _ = _assertion_formulas(tokens)
    for index, token in enumerate(tokens):
        if _starts_condition(tokens, index):
            return index
        if token in _BLOCKERS and index not in exempt and not _is_restriction(tokens, index):
            return index
    return len(tokens)


def _nearest_domain(tokens: Sequence[str], index: int, domains: Domains) -> str | None:
    """Retourne le domaine dont un nom est le plus proche du verbe, ou None s'il n'y en a aucun.

    À distance égale, le nom qui suit le verbe l'emporte (« ajouté la tâche »), puis l'ordre des
    domaines.
    """
    best: tuple[int, int, int, str] | None = None
    first = max(0, index - NEAR_DISTANCE)
    last = min(len(tokens), index + NEAR_DISTANCE + 1)
    for order, (name, domain) in enumerate(domains.items()):
        for noun in domain.nouns:
            words = _tokens(noun)
            for start in range(first, last):
                if words and _matches_at(tokens, start, words):
                    key = (abs(start - index), 0 if start > index else 1, order, name)
                    best = key if best is None or key < best else best
    return None if best is None else best[3]


def _fallback_claim(verb_class: str, domains: Domains, text: str) -> Claim | None:
    """Affirmation d'un verbe d'action sans nom de domaine à portée.

    Une classe déclarée par un seul domaine vaut l'effet de ce domaine ; par plusieurs, elle
    est générique et un reçu réussi de n'importe lequel de ces effets la prouve ; déclarée par
    aucun, il n'y a rien à affirmer ni à prouver.
    """
    declaring = [domain for domain in domains.values() if verb_class in domain.actions]
    if not declaring:
        return None
    if len(declaring) == 1:
        return Claim(declaring[0].actions[verb_class], text)
    effects = tuple(dict.fromkeys(domain.actions[verb_class] for domain in declaring))
    return Claim(f"*.{verb_class}", text, any_of=effects)


def _claims_of(proposition: str, domains: Domains) -> list[Claim]:
    """Retourne les affirmations d'une proposition ; effect None est la règle générique."""
    tokens = _tokens(proposition)
    limit = _claim_limit(tokens)
    present = _present_actions(tokens)
    _, formula_verbs = _assertion_formulas(tokens)
    claims: list[Claim] = []
    if _GENERIC_DONE.search(" ".join(tokens[:limit])):
        claims.append(Claim(None, proposition))
    for index in range(limit):
        verb_class = present.get(index) or _participle_class(tokens, index)
        if verb_class is None or index in formula_verbs:
            continue
        name = _nearest_domain(tokens, index, domains)
        if name is None:
            fallback = _fallback_claim(verb_class, domains, proposition)
            if fallback is not None:
                claims.append(fallback)
            continue
        effect = domains[name].actions.get(verb_class)
        if effect:
            claims.append(Claim(effect, proposition))
        else:
            claims.append(Claim(f"{name}.{verb_class}", proposition, declared=False))
    return claims


def detect_claims(reply: str, domains: Domains) -> list[Claim]:
    """Retourne les affirmations d'action de la réponse, sans doublon."""
    claims: dict[Claim, None] = {}
    for proposition in _affirmative_propositions(reply):
        for claim in _claims_of(proposition, domains):
            claims[claim] = None
    return list(claims)


def _proves(receipt: Receipt, claim: Claim) -> bool:
    """Indique si un reçu réussi prouve l'affirmation ; une lecture ne prouve aucune écriture."""
    if claim.effect is None:
        return receipt.effect is not None
    if claim.any_of:
        return receipt.effect in claim.any_of
    return receipt.effect == claim.effect


def verify(reply: str, receipts: Sequence[Receipt], domains: Domains) -> Verification:
    """Confronte la réponse aux reçus du tour ; seul le statut ok compte comme preuve.

    Une affirmation non déclarée par son domaine n'est jamais prouvée, quels que soient les reçus.
    """
    claims = detect_claims(reply, domains)
    successful = [receipt for receipt in receipts if receipt.status is ReceiptStatus.OK]
    unsupported: list[Claim] = []
    supported_by: dict[Claim, list[Receipt]] = {}
    for claim in claims:
        proofs = (
            [receipt for receipt in successful if _proves(receipt, claim)] if claim.declared else []
        )
        if proofs:
            supported_by[claim] = proofs
        else:
            unsupported.append(claim)
    return Verification(claims=claims, unsupported=unsupported, supported_by=supported_by)


def _describe(claim: Claim) -> str:
    if not claim.declared:
        return (
            f"- « {claim.text} » (action « {claim.effect} » : aucun outil ne la déclare, "
            "donc rien ne peut la prouver)"
        )
    if claim.any_of:
        return (
            f"- « {claim.text} » (action sans objet précisé : il faut un reçu réussi de "
            f"l'un de ces effets : {', '.join(claim.any_of)})"
        )
    return f"- « {claim.text} » (effet attendu : {claim.effect or 'une action d’écriture'})"


def correction_message(verification: Verification) -> str | None:
    """Rédige la consigne de correction pour le modèle ; None si tout est prouvé."""
    if not verification.unsupported:
        return None
    lines = "\n".join(_describe(claim) for claim in verification.unsupported)
    return (
        "Ta réponse affirme des actions qu'aucun reçu d'outil réussi ne prouve :\n"
        f"{lines}\n"
        "Appelle l'outil nécessaire, ou réécris ta réponse en disant ce qui a réellement été "
        "fait, ce qui a échoué et ce qui attend une validation."
    )
