# Format des fichiers d'évaluation du vérificateur

Un fichier d'éval est un YAML autonome : des **domaines** (les objets et ce que les verbes y
produisent), puis des **phrases annotées**. Le runner le mesure avec :

```powershell
uv run python evals/run_verifier_eval.py chemin\jeu.yaml          # détail des erreurs
uv run python evals/run_verifier_eval.py --test chemin\aveugle.yaml  # agrégats seulement
```

`--test` accepte un fichier n'importe où, hors du dépôt, et n'affiche jamais une phrase (voir
l'[ADR 0004](adr/0004-jeu-de-test-cache.md)). Le fichier doit se suffire à lui-même : il n'a besoin
d'aucun autre fichier du dépôt.

## En-tête modèle (agenda, mail, tâches)

Copiez cet en-tête tel quel, puis ajoutez vos phrases sous `cases:`.

```yaml
domains:
  calendar:
    nouns: [événement, agenda, rendez-vous, event, calendar]
    # Convention d'annotation : annuler ou supprimer un rendez-vous reste une écriture d'agenda.
    actions: {write: calendar.write, delete: calendar.write}
  email:
    nouns: [mail, e-mail, email]
    actions: {send: email.send}
  tasks:
    nouns: [tâche, task, todo, to-do, liste de tâches]
    actions: {write: tasks.write, delete: tasks.delete}

cases:
  # Une phrase qui affirme une action : expected liste les effets réellement affirmés.
  - reply: "Exemple : j'ai ajouté ce rendez-vous à ton agenda."
    expected: [calendar.write]
  # Une phrase qui n'affirme rien (lecture, intention, négation, question…) : liste vide.
  - reply: "Exemple : tu as un événement demain."
    expected: []
  # Plusieurs actions dans une phrase : tous les effets, dans n'importe quel ordre.
  - reply: "Exemple : tâche ajoutée et mail envoyé à Sam."
    expected: [tasks.write, email.send]
    group: exemples   # facultatif : "base" par défaut ; sert à mesurer des sous-ensembles
```

Les trois phrases d'exemple servent à vérifier le format ; remplacez-les par les vôtres.

## Les clés

| Clé | Rôle |
| --- | --- |
| `domains` | Un domaine par famille d'objets. Son nom est libre (`calendar`, `email`, `tasks`…). |
| `domains.<nom>.nouns` | Les noms qui désignent ses objets, au singulier (le pluriel en `s`/`x` est accepté). Plusieurs mots possibles : `liste de tâches`. |
| `domains.<nom>.actions` | Pour chaque **classe de verbe** que le domaine gère, l'effet qu'elle produit. |
| `cases[].reply` | La phrase (obligatoire, non vide). |
| `cases[].expected` | Les effets affirmés (obligatoire : un oubli ne vaut pas « aucun », écrivez `[]`). |
| `cases[].group` | Étiquette facultative pour mesurer des sous-ensembles (`base` par défaut). |

Toute autre clé est refusée, ce qui attrape les fautes de frappe.

## Les classes de verbes

Le cœur d'ATLAS connaît les verbes, rangés en trois classes. Un fichier d'éval ne les liste pas :
il dit seulement, par domaine, ce que chaque classe produit.

| Classe | Sens | Exemples d'usage |
| --- | --- | --- |
| `write` | créer, ajouter, modifier, déplacer, marquer comme fait | « j'ai ajouté », « déplacé », « coché » |
| `delete` | supprimer, annuler, retirer | « supprimée », « annulé », « retirée » |
| `send` | envoyer, transmettre, répondre | « envoyé », « transmis », « parti » |

Une autre classe est refusée avec la liste des classes connues.

## Effets attendus et classe non déclarée

- `expected` ne peut citer qu'un effet que les `actions` d'un domaine déclarent (`calendar.write`,
  `email.send`…), ou un effet `<domaine>.<classe>` pour une classe que ce domaine ne déclare pas.
- Quand un verbe d'une classe que le domaine ne déclare pas se trouve près de l'un de ses noms,
  le vérificateur signale l'affirmation avec l'effet `<domaine>.<classe>` (par exemple
  `email.write` pour « le mail est rédigé » si `email` ne déclare que `send`). Aucun outil ne peut
  la prouver, et elle n'est jamais attribuée à un autre domaine.
- Si vous annotez une phrase de ce genre comme **n'affirmant rien**, le runner la comptera comme
  fausse alerte : c'est voulu, la règle est stricte. Annotez selon ce que la phrase dit, pas
  selon ce que le vérificateur sait faire.

## Bonnes pratiques pour un jeu aveugle

1. Rédigez les phrases **avant** de connaître les règles : n'ouvrez ni `core/lexicon.py` ni
   `core/verifier.py`.
2. Annotez ce que la phrase *affirme* : l'assistant dit avoir fait l'action (ou la fait à l'instant).
   Les descriptions d'état, les actions d'un tiers, les lectures, intentions, propositions,
   questions, conditions, négations et échecs sont `[]`.
3. Visez au moins 10 phrases positives **et** 10 négatives par effet : en dessous, le runner marque
   la catégorie « effectif insuffisant ».
4. Gardez le fichier hors du dépôt, et ne le mesurez qu'avec `--test`.
