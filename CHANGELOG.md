# Journal des modifications

Toutes les modifications notables de ce projet sont consignées dans ce fichier.

Le format s'appuie sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet suit le [versionnage sémantique](https://semver.org/lang/fr/).

## [Non publié]

### Ajouté

- `core/lexicon.py` : classes de verbes write, delete et send, en français et en anglais, source
  unique des verbes du vérificateur.
- `docs/eval-format.md` : format des fichiers d'éval, avec un en-tête modèle pour un jeu externe.
- ADR 0005 : les verbes au cœur, les objets dans les compétences.
- Séparation développement / test du vérificateur : `evals/split_cases.py` découpe le jeu indépendant
  en `verifier_dev.yaml` et `verifier_test.yaml` (50/50, stratifié par effet, graine fixe).
- Le runner d'éval affiche les chiffres par effet, mesure le jeu de test sans jamais en montrer les
  phrases (`--test`) et vérifie des planchers (`--check`) définis dans `evals/thresholds.yaml`.
- CI : planchers du développement à chaque push ; planchers du test (agrégats seulement) sur les
  tags `v*` uniquement.
- Les rapports d'éval affichent toujours les effectifs (x/n) et marquent « effectif insuffisant » toute
  catégorie de moins de 10 phrases.
- `run_verifier_eval.py --test CHEMIN` mesure un jeu hors du dépôt, sans jamais afficher de phrase.
- Règle dans `CLAUDE.md` : ne jamais lire ni utiliser le jeu de test pour modifier le vérificateur.
- ADR 0004 sur le jeu de test caché.

## [0.1.0] - 2026-11-04

### Ajouté

- Squelette du paquet `atlas` en layout `src/`, géré avec uv.
- Configuration par `.env` avec la classe `Settings`.
- Commande `atlas version`.
- Outillage : ruff, mypy strict, pytest, pre-commit et intégration continue GitHub Actions.
- Documentation d'architecture et premier ADR.
- Contrat de compétence : décorateur `@tool`, classe `Skill`, `ToolSpec` et appel validé `call`.
- Manifeste `SkillManifest` et chargeur de compétences tolérant aux erreurs.
- Compétence `clock` avec l'outil `clock_now`.
- Commande `atlas skills`.
- Guide `docs/writing-a-skill.md`.
- Reçus d'outils (`Receipt`, `ReceiptStore`, `InMemoryReceiptStore`) et exécuteur `ToolExecutor`.
- Vérificateur d'affirmations d'action fondé sur les reçus.
- Champ `claims` des manifestes et méthode `SkillRegistry.claim_vocabulary()`.
- Convention d'effet `domaine.action`, validée dans les manifestes et par `@tool`.
- Refus au chargement des effets de `requires_approval` et `claims` qu'aucun outil ne déclare.
- Méthode `SkillRegistry.requires_approval()`, utilisée directement par `ToolExecutor`.
- Jeu d'évaluation `evals/verifier_cases.yaml` et script `evals/run_verifier_eval.py`.
- Interface `LLMClient` avec `OpenRouterClient`, `OllamaClient` et `ScriptedLLM` (tests sans réseau).
- Nouvelles tentatives sur 429 et 5xx, messages propres aux erreurs 400, 401, 402, 403 et 404.
- Réglages du modèle, de la boucle et des budgets quotidiens (tokens et dollars) dans `Settings`.
- Boucle agent `Agent` : exécution de tous les appels d'outils, contrôle des affirmations,
  une correction puis blocage, messages honnêtes sans exception.
- Journal de consommation `UsageLog` avec coût réel (`usage.cost`) et budgets quotidiens.
- Commande `atlas chat` avec reçus, verdict en couleur, tokens et coût.
- ADR 0003 sur la boucle agent et la confidentialité d'OpenRouter.
- Le vérificateur reconnaît le présent à la 1re personne (« j'ajoute », « I'm adding »), les
  formules « je te confirme que », « je t'assure que » et « I can confirm », et traite
  « ne … que » comme une restriction.
- Les cas de l'éval ont un groupe (`base`, `variantes`) mesuré séparément par le script.
- Réglage `ATLAS_ADDRESS_FORM` (`tu` ou `vous`, défaut `tu`) injecté dans le prompt système.
- Stockage SQLite (`ATLAS_DATABASE_PATH`) avec migrations numérotées appliquées dans une
  transaction ; migrations du cœur et migrations propres à chaque compétence.
- `SqliteReceiptStore`, store par défaut de `atlas chat`, et journal de consommation en SQLite.
- `SkillContext` (connexion et réglages) transmis aux compétences à leur création.
- Compétence `tasks` : `add_task`, `list_tasks`, `complete_task`, `delete_task` ; la suppression
  demande une validation humaine. Scénarios dans `evals/tasks.yaml`.
- Approbation dans `atlas chat` : « Approuver ? (o/n) », nouveau reçu lié au reçu en attente,
  statut `rejected` en cas de refus.
- Les bases `*.db` (et leurs fichiers WAL) sont ignorées par git.

### Modifié

- **Format de `claims` dans les manifestes** : `nouns` (objets) + `actions` (classe de verbe → effet)
  remplacent « effet → mots ». Chaque effet cité doit être déclaré par un outil, chaque classe doit
  être connue du cœur. Le manifeste de `tasks` est mis à jour. `SkillRegistry.claim_vocabulary()`
  devient `claim_domains()`.
- **Vérificateur** : l'effet est la classe du verbe appliquée au domaine du nom le plus proche ; une
  classe que le domaine ne déclare pas est signalée `<domaine>.<classe>` et ne se prouve jamais.
  Seuls les verbes du lexique comptent (plus de participe en -é quelconque).
- **Fichiers d'éval** : `domains` remplace `vocabulary` (fichiers du dépôt convertis).
- Planchers du développement recalés après la refonte (calendar.write, détection : 66,7 % → 64,1 %).
- Vocabulaire `tasks.delete` : `retiré`, `retirée`, `retirés`, `retirées` (`removed` y figurait déjà).
- `README.md` de présentation : résultats mesurés sur les deux jeux d'évaluation, architecture,
  démarrage, limites et feuille de route.
- Le vérificateur traite un verbe qui figure dans le vocabulaire d'un effet comme désignant cet
  effet seul (l'éval est inchangée).
- `atlas chat` affiche les réponses en Markdown, avec une ligne vide avant les reçus et le verdict.
- Le prompt système demande des réponses concises et interdit de revenir sur un sujet clos.
- `ToolExecutor` transforme `SystemExit` en reçu `error` et accepte des arguments illisibles.
- Le vérificateur traite les conditions comme des marqueurs bloquants pour le reste de la phrase.

### Corrigé

- Le dossier `data/` est désormais ignoré par git.
