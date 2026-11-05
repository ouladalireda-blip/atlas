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
  nouns:
    - note
    - mémo
  actions:
    write: notes.write
```

- Toutes les clés sont obligatoires sauf `claims`, et toute autre clé est refusée.
- `name` contient uniquement des minuscules, des chiffres et des `_`, et commence par une lettre.
- Un effet s'écrit `domaine.action`, en minuscules : `calendar.write`, `email.send`.
- `requires_approval` liste les effets soumis à validation humaine. Laissez `[]` pour une
  compétence en lecture seule.
- `requires_approval` et `claims` ne citent que des effets déclarés par un outil de la compétence.
  Sinon la compétence est refusée au chargement, pour qu'une faute de frappe ne supprime pas une
  validation.
- `claims` dit comment vérifier les affirmations de la compétence. Le cœur connaît les **verbes**
  (`core/lexicon.py`, classes `write`, `delete`, `send`, en français et en anglais) ; la compétence
  ne déclare que ses **objets** et ce que chaque classe produit chez elle :
  - `nouns` : les noms de ses objets (« note », « mémo »), au singulier ;
  - `actions` : pour chaque classe de verbe qu'elle gère, l'effet produit (`write: notes.write`).
  Déclarez-le pour tout effet : sans `claims`, ses affirmations ne sont pas vérifiées. Chaque effet
  cité doit être déclaré par un outil, et chaque classe doit être connue du cœur, sinon la
  compétence est refusée au chargement.
- Le vérificateur lit l'effet d'une phrase ainsi : classe du verbe + domaine du nom le plus proche.
  « La note est supprimée » donne la classe `delete` ; si `actions` n'a pas de `delete`, l'affirmation
  est signalée comme `notes.delete`, que rien ne peut prouver, et jamais attribuée à une autre
  compétence. Sans nom à portée (« C'est envoyé ! »), le verbe vaut l'effet de la seule compétence qui déclare
  sa classe, ou une affirmation générique prouvée par un reçu de n'importe laquelle quand plusieurs la
  déclarent. Il n'y a donc aucun verbe à lister dans le manifeste. Si un verbe manque, ajoutez-le
  une fois à `core/lexicon.py` : toutes les compétences en profitent.

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

## Stocker des données (facultatif)

Une compétence qui garde des données reçoit un `SkillContext` à sa création. Elle n'ouvre jamais
la base elle-même : le cœur l'ouvre, applique les migrations, puis lui donne la connexion.

```python
from atlas.skills.base import Skill, SkillContext, tool


class NotesSkill(Skill):
    name = "notes"

    def __init__(self, context: SkillContext | None = None) -> None:
        super().__init__(context)
        # Échoue au chargement, avec un message clair, si le contexte manque.
        self._db = self.require_context().connection

    @tool(effect="notes.write")
    def add(self, text: str) -> str:
        """Enregistre une note."""
        # Requêtes toujours paramétrées (?), jamais de texte collé dans le SQL.
        self._db.execute("INSERT INTO notes (text) VALUES (?)", (text,))
        return f"Note enregistrée : {text}"
```

Le schéma vit dans `src/atlas/skills/notes/migrations/`, un fichier par évolution :

```sql
-- 0001_create_notes.sql
CREATE TABLE notes (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    text TEXT NOT NULL
);
```

- Les fichiers se nomment `NNNN_nom.sql` : quatre chiffres, à partir de `0001`, sans trou ni
  doublon, nom en minuscules. Le cœur découvre le dossier `migrations/` tout seul.
- Chaque migration s'exécute dans une transaction, avec l'inscription de sa version : si une
  instruction échoue, tout est annulé et ATLAS refuse de démarrer en nommant le fichier fautif.
- N'écrivez ni `BEGIN` ni `COMMIT` (refusés) et ne modifiez jamais une migration déjà appliquée :
  son empreinte est vérifiée. Pour changer le schéma, ajoutez `0002_...sql`.
- La numérotation est propre à chaque compétence : la vôtre commence à `0001`, quoi qu'en disent
  les autres. Les tables d'une compétence portent un nom qui ne heurte pas celles des autres.
- Les migrations d'une compétence qui échoue au chargement ne sont pas appliquées.
- Dans les tests, utilisez `connect(":memory:")` et `apply_migrations` avec
  `MigrationSource("notes", <dossier migrations>)` (voir `skills/tasks/tests/`).

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
