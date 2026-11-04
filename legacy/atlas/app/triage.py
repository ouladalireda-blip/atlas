"""Le trieur : un classifieur, pas un LLM.

Il lit chaque message AVANT le modèle et choisit dans un ensemble fixe :
quel effort (light / standard / deep), quels groupes d'outils charger,
faut-il faire un rappel mémoire, faut-il extraire des faits ensuite.
TF-IDF (caractères) + régression logistique multi-label : ~1 ms, zéro coût d'API.
Des règles à haute précision complètent les prédictions.
"""
from __future__ import annotations

import re
from dataclasses import asdict, dataclass

from sklearn.feature_extraction.text import TfidfVectorizer
from sklearn.linear_model import LogisticRegression
from sklearn.multiclass import OneVsRestClassifier
from sklearn.preprocessing import MultiLabelBinarizer

from .config import settings

LABELS = ["calendar", "tasks", "memory", "notes", "email", "deep", "smalltalk"]

SEED: list[tuple[str, list[str]]] = [
    ("ajoute un rdv demain à 14h avec le dentiste", ["calendar"]),
    ("mets une réunion jeudi 10h dans mon agenda", ["calendar"]),
    ("qu'est-ce que j'ai cette semaine ?", ["calendar"]),
    ("je suis libre vendredi après-midi ?", ["calendar"]),
    ("annule mon rendez-vous de lundi", ["calendar"]),
    ("décale l'entretien SNCF à 16h", ["calendar"]),
    ("add a meeting with Sarah tomorrow at 3pm", ["calendar"]),
    ("what's on my calendar today", ["calendar"]),
    ("planifie un créneau de sport mercredi soir", ["calendar"]),
    ("bloque 2h pour réviser samedi matin", ["calendar"]),
    ("rappelle-moi de payer le loyer", ["tasks"]),
    ("ajoute à ma todo : envoyer le CV à Capgemini", ["tasks"]),
    ("qu'est-ce qu'il me reste à faire ?", ["tasks"]),
    ("marque la tâche 3 comme faite", ["tasks"]),
    ("liste mes tâches en cours", ["tasks"]),
    ("add buy groceries to my todo list", ["tasks"]),
    ("j'ai fini le dossier CAF, coche-le", ["tasks"]),
    ("crée une tâche relancer le recruteur pour vendredi", ["tasks"]),
    ("tu te souviens de ce que je t'ai dit sur mon stage ?", ["memory"]),
    ("retiens que ma sœur s'appelle Imane", ["memory"]),
    ("c'était quoi le nom du restaurant dont on a parlé ?", ["memory"]),
    ("qu'est-ce que tu sais sur moi ?", ["memory"]),
    ("oublie ce que je t'ai dit sur mon ancien job", ["memory"]),
    ("remember that I'm allergic to nothing but hate cilantro", ["memory"]),
    ("what did I tell you about my thesis last week?", ["memory"]),
    ("je préfère les réponses courtes, retiens-le", ["memory"]),
    ("mon manager s'appelle Karim et il est exigeant", ["memory"]),
    ("je travaille sur un projet RFM sous KNIME", ["memory"]),
    ("écris une note sur la procédure de sauvegarde du serveur", ["notes"]),
    ("fais un portrait de Karim dans tes notes", ["notes"]),
    ("relis ta note sur ma recherche d'alternance", ["notes"]),
    ("écris-toi une lettre pour le mois prochain", ["notes"]),
    ("liste tes notes", ["notes"]),
    ("write down the steps to deploy the app in your notes", ["notes"]),
    ("prépare un mail au recruteur pour le remercier", ["email"]),
    ("envoie un email à karim@exemple.fr pour confirmer jeudi", ["email"]),
    ("rédige une relance par mail pour l'offre data analyst", ["email"]),
    ("draft an email to my landlord about the heating", ["email"]),
    ("réponds au mail de l'école pour dire que je serai absent", ["email"]),
    ("compare trois stratégies pour décrocher une alternance en data et recommande-en une", ["deep"]),
    ("analyse les forces et faiblesses de mon projet de portail B2B", ["deep"]),
    ("explique-moi en détail comment fonctionne le prompt caching et ses compromis", ["deep"]),
    ("aide-moi à structurer un plan de révision sur 3 semaines pour mes partiels", ["deep"]),
    ("réfléchis aux risques de mon architecture avant que je la déploie", ["deep"]),
    ("write a thorough comparison of pgvector vs qdrant for my use case", ["deep"]),
    ("planifie ma semaine : 3 candidatures, 2 séances de sport et le projet business strategy", ["deep", "calendar", "tasks"]),
    ("prépare mon entretien de jeudi : ajoute-le à l'agenda et fais-moi une fiche", ["calendar", "notes", "deep"]),
    ("ajoute le rdv avec Karim et préviens-le par mail", ["calendar", "email"]),
    ("note dans mes tâches de relire la note sur SOTHEMA", ["tasks", "notes"]),
    ("salut", ["smalltalk"]),
    ("bonjour ça va ?", ["smalltalk"]),
    ("merci !", ["smalltalk"]),
    ("hello", ["smalltalk"]),
    ("ok parfait", ["smalltalk"]),
    ("haha bien vu", ["smalltalk"]),
    ("bonne nuit", ["smalltalk"]),
    ("thanks, that's great", ["smalltalk"]),
    ("tu es là ?", ["smalltalk"]),
    ("super, merci beaucoup", ["smalltalk"]),
]

# Règles haute précision (complètent le modèle statistique)
RULES = {
    "calendar": re.compile(r"\b(agenda|calendrier|calendar|rdv|rendez-vous|réunion|meeting|créneau|entretien à)\b", re.I),
    "tasks": re.compile(r"\b(t[âa]che|todo|to-do|à faire|rappelle-moi|reminder)\b", re.I),
    "memory": re.compile(r"\b(souviens|retiens|rappelles-tu|oublie|remember|forget|tu sais sur moi)\b", re.I),
    "notes": re.compile(r"\b(note|notes|portrait|procédure|lettre)\b", re.I),
    "email": re.compile(r"\b(e-?mail|mail|courriel|relance par mail)\b", re.I),
}
DURABLE = re.compile(
    r"\b(je suis|j'habite|je vis|je travaille|mon (?:frère|père|manager|chef|ami|copain)|ma (?:sœur|soeur|mère|copine|amie)"
    r"|je préfère|j'aime|je déteste|i am|i'm|i live|i work|my (?:sister|brother|manager|wife|husband|friend)|i prefer)\b",
    re.I,
)


@dataclass
class Triage:
    effort: str            # light | standard | deep
    groups: list[str]      # groupes d'outils à charger
    labels: list[str]
    recall: bool
    extract_facts: bool
    model: str

    def as_dict(self) -> dict:
        return asdict(self)


class Triager:
    def __init__(self, threshold: float = 0.35):
        self.threshold = threshold
        texts = [t for t, _ in SEED]
        self.mlb = MultiLabelBinarizer(classes=LABELS)
        y = self.mlb.fit_transform([labels for _, labels in SEED])
        self.vec = TfidfVectorizer(analyzer="char_wb", ngram_range=(2, 5), sublinear_tf=True, lowercase=True)
        x = self.vec.fit_transform(texts)
        self.clf = OneVsRestClassifier(LogisticRegression(C=8.0, max_iter=2000, class_weight="balanced"))
        self.clf.fit(x, y)

    def predict_labels(self, text: str) -> list[str]:
        probs = self.clf.predict_proba(self.vec.transform([text]))[0]
        labels = {LABELS[i] for i, p in enumerate(probs) if p >= self.threshold}
        for label, rx in RULES.items():
            if rx.search(text):
                labels.add(label)
        if not labels:
            labels.add(LABELS[int(probs.argmax())])
        if labels - {"smalltalk"}:
            labels.discard("smalltalk")
        return sorted(labels)

    def __call__(self, text: str) -> Triage:
        labels = self.predict_labels(text)
        groups = [g for g in labels if g in {"calendar", "tasks", "memory", "notes", "email"}]
        long_ask = len(text) > 280
        if labels == ["smalltalk"]:
            effort, model = "light", settings.model_fast
        elif "deep" in labels or long_ask:
            effort, model = "deep", settings.model_deep
        else:
            effort, model = "standard", settings.model_main
        if effort != "light":
            groups = sorted(set(groups) | {"memory"})  # toujours pouvoir chercher en mémoire
        return Triage(
            effort=effort,
            groups=groups,
            labels=labels,
            recall=effort != "light",
            extract_facts=bool(DURABLE.search(text)) or "memory" in labels,
            model=model,
        )
