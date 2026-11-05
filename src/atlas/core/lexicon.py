"""Lexique des verbes d'action : la seule source de vérité des verbes du vérificateur.

Le cœur connaît les verbes, rangés en classes (write, delete, send), en français et en anglais.
Les compétences ne connaissent que leurs objets (« tâche », « rendez-vous ») et disent, pour
chaque classe, quel effet elle produit chez elles (voir `Domain`). Ajouter un verbe ici profite à
tous les domaines.

Formes reconnues pour chaque verbe :

- les participes passés, au masculin, au féminin et au pluriel (« supprimé, supprimée, supprimés,
  supprimées »), y compris irréguliers (« mis, mise, mises », « transmis », « parti ») ; le passé
  composé (« j'ai ajouté », « elle a été supprimée ») est reconnu par son participe ;
- le présent de l'indicatif à la 1re personne du singulier (« j'ajoute », « je mets », « I add ») ;
- en anglais, le prétérit et le participe (« added », « wrote », « written ») et la forme en -ing
  (« I'm adding »).

Les formes sont en minuscules, sans apostrophe : le vérificateur découpe « j'ajoute » en
« j » et « ajoute ».
"""

from collections.abc import Mapping
from dataclasses import dataclass


@dataclass(frozen=True)
class Domain:
    """Un domaine métier : les noms de ses objets et l'effet de chaque classe de verbe.

    actions associe une classe de verbe (« write ») à l'effet que ce domaine lui donne
    (« tasks.write »). Une classe absente n'est pas gérée par le domaine : un verbe de cette
    classe près d'un de ses noms est signalé comme affirmation non prouvable.
    """

    nouns: tuple[str, ...]
    actions: Mapping[str, str]


@dataclass(frozen=True)
class VerbClass:
    """Les formes d'une classe de verbes."""

    name: str
    participles: frozenset[str]
    present_fr: frozenset[str]
    present_en: frozenset[str]
    progressive_en: frozenset[str]


def _fr_regular(stems: str, *, present: bool = True) -> tuple[set[str], set[str]]:
    """Verbes en -er à radical stable : radical + é/ée/és/ées au participe, + e au présent."""
    participles: set[str] = set()
    forms: set[str] = set()
    for stem in stems.split():
        participles |= {stem + "é", stem + "ée", stem + "és", stem + "ées"}
        if present:
            forms.add(stem + "e")
    return participles, forms


def _fr_irregular(spec: str) -> set[str]:
    """Participes irréguliers : une entrée « masc/fém/masc.pl/fém.pl » par verbe."""
    return {form for entry in spec.split() for form in entry.split("/")}


def _en(spec: str) -> tuple[set[str], set[str], set[str]]:
    """Verbes anglais « base:participes:formes-ing » ; plusieurs formes avec des virgules."""
    participles: set[str] = set()
    bases: set[str] = set()
    progressive: set[str] = set()
    for entry in spec.split():
        base, past, ing = entry.split(":")
        bases.add(base)
        participles |= set(past.split(","))
        progressive |= set(ing.split(","))
    return participles, bases, progressive


def _build(
    name: str,
    *,
    fr_regular: str,
    fr_accent: str = "",
    fr_irregular: str = "",
    fr_present: str = "",
    en: str,
) -> VerbClass:
    regular_participles, regular_present = _fr_regular(fr_regular)
    accent_participles, _ = _fr_regular(fr_accent, present=False)
    en_participles, en_bases, en_progressive = _en(en)
    return VerbClass(
        name=name,
        participles=frozenset(
            regular_participles | accent_participles | _fr_irregular(fr_irregular) | en_participles
        ),
        present_fr=frozenset(regular_present | set(fr_present.split())),
        present_en=frozenset(en_bases),
        progressive_en=frozenset(en_progressive),
    )


_WRITE = _build(
    "write",
    fr_regular=(
        "ajout cré programm planifi réserv bloqu cal not enregistr modifi déplac décal report "
        "ajust fix confirm valid marqu coch termin finalis actualis remplac renomm corrig "
        "synchronis sauvegard archiv rédig"
    ),
    fr_accent="complét",  # présent « complète » : donné à part
    fr_irregular=("mis/mise/mises inscrit/inscrite/inscrits/inscrites écrit/écrite/écrits/écrites"),
    fr_present="mets inscris écris complète",
    en=(
        "add:added:adding create:created:creating schedule:scheduled:scheduling "
        "reschedule:rescheduled:rescheduling book:booked:booking set:set:setting put:put:putting "
        "make:made:making write:written,wrote:writing save:saved:saving update:updated:updating "
        "move:moved:moving mark:marked:marking register:registered:registering "
        "record:recorded:recording log:logged:logging rename:renamed:renaming "
        "replace:replaced:replacing fix:fixed:fixing confirm:confirmed:confirming "
        "plan:planned:planning note:noted:noting complete:completed:completing "
        "block:blocked:blocking tick:ticked:ticking enter:entered:entering "
        "finish:finished:finishing"
    ),
)
_DELETE = _build(
    "delete",
    fr_regular="supprim effac retir enlev annul ray vid décoch",
    fr_irregular="détruit/détruite/détruits/détruites",
    fr_present="détruis enlève",
    en=(
        "delete:deleted:deleting remove:removed:removing "
        "cancel:cancelled,canceled:cancelling,canceling "
        "erase:erased:erasing clear:cleared:clearing drop:dropped:dropping"
    ),
)
_SEND = _build(
    "send",
    fr_regular="expédi relanc adress notifi",
    fr_accent="transfér",  # présent « transfère » : donné à part
    fr_irregular=(
        "envoyé/envoyée/envoyés/envoyées transmis/transmise/transmises "
        "répondu/répondue/répondus/répondues parti/partie/partis/parties "
        "prévenu/prévenue/prévenus/prévenues"
    ),
    fr_present="envoie transmets réponds préviens transfère",
    en=(
        "send:sent:sending email:emailed:emailing mail:mailed:mailing "
        "forward:forwarded:forwarding reply:replied:replying deliver:delivered:delivering "
        "notify:notified:notifying respond:responded:responding"
    ),
)

LEXICON: Mapping[str, VerbClass] = {verb.name: verb for verb in (_WRITE, _DELETE, _SEND)}
# Les noms de classes que les manifestes et les jeux d'éval ont le droit de citer.
VERB_CLASSES: frozenset[str] = frozenset(LEXICON)


def _index(attribute: str) -> Mapping[str, str]:
    """Associe chaque forme à sa classe ; une forme dans deux classes est une erreur de lexique."""
    index: dict[str, str] = {}
    for verb in LEXICON.values():
        for form in getattr(verb, attribute):
            if index.setdefault(form, verb.name) != verb.name:
                raise ValueError(
                    f"Lexique ambigu : « {form} » est dans {index[form]} et dans {verb.name}."
                )
    return index


PARTICIPLE_CLASS = _index("participles")
PRESENT_FR_CLASS = _index("present_fr")
PRESENT_EN_CLASS = _index("present_en")
PROGRESSIVE_EN_CLASS = _index("progressive_en")
