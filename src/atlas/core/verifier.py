"""Vérification des affirmations d'action d'une réponse à partir des reçus d'outils.

Les listes de ce module ne contiennent que de la grammaire française et anglaise. Le
vocabulaire métier vient des manifestes des compétences.
"""

import re
import unicodedata
from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from atlas.core.receipts import Receipt, ReceiptStatus

type ClaimVocabulary = Mapping[str, Sequence[str]]

# Écart maximal, en mots, entre un participe passé et un mot du vocabulaire.
NEAR_DISTANCE = 6

_CODE = re.compile(r"```.*?(?:```|\Z)|`[^`\n]*`", re.DOTALL)
_SENTENCE_BREAK = re.compile(r"(?<=[.!?…])\s+|\n+")
_CLAUSE_BREAK = re.compile(r"[,;:()]|\s[-–—]\s|\b(?:mais|donc|car|but|so)\b", re.IGNORECASE)
_COORDINATION = re.compile(r"\b(?:et|puis|and|then)\b", re.IGNORECASE)
_WORD = re.compile(r"\w+")
_GENERIC_DONE = re.compile(r"\bc est (?:bien |déjà )?fait\b|\bdone\b")
_SENTENCE_CLOSERS = " \t!*_\"'»)"

_PARTICIPLE_ENDINGS = ("é", "ée", "és", "ées", "ed")
_IRREGULAR_PARTICIPLES = frozenset(
    "mis mise mises pris prise prises écrit écrite écrits écrites inscrit inscrite inscrits "
    "inscrites parti partis sent made set put written wrote paid built".split()
)
# Formes en -é ou -ed qui n'annoncent pas une action accomplie : auxiliaire, échec, excuse,
# lecture, demande, ou simple mot qui a la même terminaison.
_NON_ACTION_STEMS = "ét échou désol consult vérifi regard cherch trouv demand souhait".split()
_NON_ACTIONS = frozenset(
    {stem + ending for stem in _NON_ACTION_STEMS for ending in ("é", "ée", "és", "ées")}
    | set(
        "failed checked looked viewed searched reviewed asked requested wanted needed based "
        "need indeed feed speed seed bed embed exceed proceed succeed red shed hundred".split()
    )
)
# Un mot en -é placé juste après un déterminant est un nom : « ta journée », « the added ».
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
# Verbes d'écriture au présent, 1re personne du singulier. Liste positive : « j'utilise ton
# agenda » ou « je lis ton agenda » décrivent une lecture, pas une action accomplie.
_WRITE_VERBS_FR = frozenset(
    """ajoute envoie crée programme planifie supprime efface annule modifie déplace enregistre
    réserve transmets mets fixe inscris écris rédige publie sauvegarde archive valide note
    renomme remplace corrige synchronise confirme poste""".split()
)
_WRITE_VERBS_EN = frozenset(
    """add send create schedule book delete remove cancel update move save set write post
    forward publish register record rename replace fix confirm""".split()
)


def _progressive_forms(verb: str) -> set[str]:
    """Retourne les formes en -ing d'un verbe anglais : save → saving, set → setting."""
    forms = {verb + "ing", verb + verb[-1] + "ing"}
    if verb.endswith("e"):
        forms.add(verb[:-1] + "ing")
    return forms


_PROGRESSIVE_EN = frozenset(form for verb in _WRITE_VERBS_EN for form in _progressive_forms(verb))

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
    """Affirmation d'action ; effect vaut None si n'importe quelle écriture la prouve."""

    effect: str | None
    text: str


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


def _is_participle(tokens: Sequence[str], index: int) -> bool:
    """Indique si le mot à cette position se lit comme un participe passé d'action."""
    token = tokens[index]
    if token in _NON_ACTIONS:
        return False
    has_form = token in _IRREGULAR_PARTICIPLES or (
        len(token) > 2 and token.endswith(_PARTICIPLE_ENDINGS)
    )
    return has_form and (index == 0 or tokens[index - 1] not in _DETERMINERS)


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
            if tokens and _is_participle(tokens, 0):
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
    """Compare un mot du vocabulaire aux mots du texte, en acceptant le pluriel."""
    chunk = tokens[start : start + len(word)]
    return len(chunk) == len(word) and all(
        token in (expected, f"{expected}s", f"{expected}x")
        for token, expected in zip(chunk, word, strict=True)
    )


def _is_near(tokens: Sequence[str], index: int, word: Sequence[str]) -> bool:
    """Indique si un mot du vocabulaire se trouve assez près de la position donnée."""
    if not word:
        return False
    first = max(0, index - NEAR_DISTANCE)
    last = min(len(tokens), index + NEAR_DISTANCE + 1)
    return any(_matches_at(tokens, start, word) for start in range(first, last))


def _present_verb_after(tokens: Sequence[str], subject: int) -> int | None:
    """Retourne la position du verbe d'écriture au présent qui suit « je » ou « i »."""
    if subject > 0 and tokens[subject - 1] in _SUBORDINATORS:
        return None
    word = tokens[subject]
    position = subject + 1
    if word in _FRENCH_SUBJECTS:
        while position < len(tokens) and tokens[position] in _CLITICS:
            position += 1
        if position < len(tokens) and tokens[position] in _WRITE_VERBS_FR:
            return position
        return None
    while position < len(tokens) and tokens[position] in _ENGLISH_ADVERBS:
        position += 1
    if position < len(tokens) and tokens[position] in {"m", "am"}:
        # « i'm scheduling » : forme progressive.
        position += 1
        return position if position < len(tokens) and tokens[position] in _PROGRESSIVE_EN else None
    if position < len(tokens) and tokens[position] in _WRITE_VERBS_EN:
        return position
    return None


def _present_actions(tokens: Sequence[str]) -> set[int]:
    """Retourne les positions des verbes d'action conjugués au présent à la 1re personne."""
    positions = (
        _present_verb_after(tokens, index)
        for index, token in enumerate(tokens)
        if token in _FRENCH_SUBJECTS or token == _ENGLISH_SUBJECT
    )
    return {position for position in positions if position is not None}


def _assertion_auxiliaries(tokens: Sequence[str]) -> set[int]:
    """Retourne les positions de « peux » ou « can » employés dans une formule d'affirmation."""
    found: set[int] = set()
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
                if auxiliary is not None:
                    found.add(auxiliary)
        elif token == _ENGLISH_SUBJECT:
            if tokens[position : position + 1] == ["can"]:
                auxiliary, position = position, position + 1
            verb = tokens[position : position + 1]
            if verb and verb[0] in _ASSERTION_VERBS_EN and auxiliary is not None:
                found.add(auxiliary)
    return found


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
    exempt = _assertion_auxiliaries(tokens)
    for index, token in enumerate(tokens):
        if _starts_condition(tokens, index):
            return index
        if token in _BLOCKERS and index not in exempt and not _is_restriction(tokens, index):
            return index
    return len(tokens)


def _effects_around(tokens: Sequence[str], index: int, vocabulary: ClaimVocabulary) -> list[str]:
    """Retourne les effets visés par le verbe ou participe à cette position.

    Si ce mot est lui-même dans le vocabulaire d'un effet (« supprimé » pour tasks.delete), il
    désigne l'action et seuls ces effets comptent. Sinon, tout effet dont un mot est proche
    convient : les noms (« tâche ») ne disent pas quelle action a eu lieu.
    """
    near = [
        effect
        for effect, words in vocabulary.items()
        if any(_is_near(tokens, index, _tokens(word)) for word in words)
    ]
    own = [
        effect
        for effect in near
        if any(_matches_at(tokens, index, _tokens(word)) for word in vocabulary[effect])
    ]
    return own or near


def _claimed_effects(tokens: Sequence[str], vocabulary: ClaimVocabulary) -> list[str | None]:
    """Retourne les effets affirmés par une proposition ; None désigne la règle générique."""
    limit = _claim_limit(tokens)
    present = _present_actions(tokens)
    effects: list[str | None] = []
    if _GENERIC_DONE.search(" ".join(tokens[:limit])):
        effects.append(None)
    for index in range(limit):
        if _is_participle(tokens, index) or index in present:
            effects.extend(_effects_around(tokens, index, vocabulary))
    return effects


def detect_claims(reply: str, vocabulary: ClaimVocabulary) -> list[Claim]:
    """Retourne les affirmations d'action de la réponse, sans doublon."""
    claims: dict[Claim, None] = {}
    for proposition in _affirmative_propositions(reply):
        for effect in _claimed_effects(_tokens(proposition), vocabulary):
            claims[Claim(effect, proposition)] = None
    return list(claims)


def _proves(receipt: Receipt, claim: Claim) -> bool:
    """Indique si un reçu réussi prouve l'affirmation ; une lecture ne prouve aucune écriture."""
    if claim.effect is None:
        return receipt.effect is not None
    return receipt.effect == claim.effect


def verify(reply: str, receipts: Sequence[Receipt], vocabulary: ClaimVocabulary) -> Verification:
    """Confronte la réponse aux reçus du tour ; seul le statut ok compte comme preuve."""
    claims = detect_claims(reply, vocabulary)
    successful = [receipt for receipt in receipts if receipt.status is ReceiptStatus.OK]
    unsupported: list[Claim] = []
    supported_by: dict[Claim, list[Receipt]] = {}
    for claim in claims:
        proofs = [receipt for receipt in successful if _proves(receipt, claim)]
        if proofs:
            supported_by[claim] = proofs
        else:
            unsupported.append(claim)
    return Verification(claims=claims, unsupported=unsupported, supported_by=supported_by)


def correction_message(verification: Verification) -> str | None:
    """Rédige la consigne de correction pour le modèle ; None si tout est prouvé."""
    if not verification.unsupported:
        return None
    lines = "\n".join(
        f"- « {claim.text} » (effet attendu : {claim.effect or 'une action d’écriture'})"
        for claim in verification.unsupported
    )
    return (
        "Ta réponse affirme des actions qu'aucun reçu d'outil réussi ne prouve :\n"
        f"{lines}\n"
        "Appelle l'outil nécessaire, ou réécris ta réponse en disant ce qui a réellement été "
        "fait, ce qui a échoué et ce qui attend une validation."
    )
