# Écrire une compétence

Une compétence ajoute des outils à ATLAS sans modifier le moteur. Ce guide crée une compétence
fictive `notes` en 4 étapes. La compétence [`clock`](../src/atlas/skills/clock/) sert d'exemple
réel.

## 1. Créer le dossier et le manifeste

Créez `src/atlas/skills/notes/` avec un fichier `__init__.py` vide et ce `manifest.yaml` :

```yaml
name: notes
version: "0.1.0"
description: Enregistre des notes personnelles.
examples:
  - Note que je dois rappeler Sam demain.
requires_approval:
  - notes.write
claims:
  notes.write:
    - note
    - mémo
```

- Toutes les clés sont obligatoires sauf `claims`, et toute autre clé est refusée.
- `name` contient uniquement des minuscules, des chiffres et des `_`, et commence par une lettre.
- Un effet s'écrit `domaine.action`, en minuscules : `calendar.write`, `email.send`.
- `requires_approval` liste les effets soumis à validation humaine. Laissez `[]` pour une
  compétence en lecture seule.
- `requires_approval` et `claims` ne citent que des effets déclarés par un outil de la compétence.
  Sinon la compétence est refusée au chargement, pour qu'une faute de frappe ne supprime pas une
  validation.
- `claims` donne, pour chaque effet, les mots qui signalent qu'une réponse affirme cette action.
  Déclarez-le pour tout effet : sans lui, le vérificateur ne contrôle pas ces affirmations. Donnez
  à chaque effet ses propres mots, car un mot partagé exige un reçu de chacun des effets.

## 2. Écrire la classe dans `skill.py`

```python
"""Compétence notes : enregistre des notes personnelles."""

from datetime import date

from atlas.skills.base import Skill, tool


class NotesSkill(Skill):
    """Gère les notes personnelles."""

    name = "notes"

    @tool(effect="notes.write")
    def add(self, text: str, due: date | None = None) -> str:
        """Enregistre une note, avec une échéance facultative."""
        return f"Note enregistrée : {text}"
```

Cet outil s'appelle `notes_add`. ATLAS en déduit son schéma JSON : `text` est requis car il n'a
pas de valeur par défaut, et `due` est facultatif.

Respectez ces règles, sinon la compétence est signalée en erreur au chargement :

- `skill.py` définit une seule classe `Skill`, et son `name` est celui du manifeste.
- Le décorateur s'écrit avec des parenthèses : `@tool()` pour un outil en lecture seule,
  `@tool(effect="...")` pour un outil qui agit.
- Chaque outil a une docstring : le modèle la lit pour décider de l'utiliser.
- Chaque paramètre a un type et se passe par nom (ni `*args` ni `**kwargs`).
- Le nom complet de chaque outil, `<compétence>_<méthode>`, est unique dans ATLAS.

Depuis `skill.py`, importez vos autres modules par leur chemin complet
(`atlas.skills.notes.helpers`) : les imports relatifs ne fonctionnent pas.

## 3. Ajouter les tests

Placez les tests dans `src/atlas/skills/notes/tests/`, avec un `__init__.py` vide. Testez chaque
outil en passant par `call`, qui applique la même validation qu'en production :

```python
from atlas.skills.base import call
from atlas.skills.notes.skill import NotesSkill


def test_add_saves_note() -> None:
    (spec,) = NotesSkill().tools()

    assert call(spec, {"text": "Rappeler Sam"}) == "Note enregistrée : Rappeler Sam"
```

Aucun test ne doit faire d'appel réseau.

## 4. Ajouter les scénarios et vérifier

Créez `evals/notes.yaml` avec au moins 3 scénarios, dont un où l'outil ne doit pas être utilisé :

```yaml
skill: notes
scenarios:
  - id: note_simple
    prompt: Note que je dois rappeler Sam.
    expected_tool: notes_add
  - id: note_avec_echeance
    prompt: Note de payer le loyer avant le 5 décembre.
    expected_tool: notes_add
  - id: question_generale
    prompt: À quoi sert une prise de notes ?
    expected_tool: null
```

Vérifiez ensuite que la compétence apparaît sans erreur, puis lancez les contrôles :

```powershell
uv run atlas skills
uv run pytest
uv run ruff check .
uv run mypy src
```
