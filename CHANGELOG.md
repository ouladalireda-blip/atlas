# Journal des modifications

Toutes les modifications notables de ce projet sont consignées dans ce fichier.

Le format s'appuie sur [Keep a Changelog](https://keepachangelog.com/fr/1.1.0/),
et ce projet suit le [versionnage sémantique](https://semver.org/lang/fr/).

## [Non publié]

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

### Modifié

- `ToolExecutor` transforme `SystemExit` en reçu `error` et accepte des arguments illisibles.
- Le vérificateur traite les conditions comme des marqueurs bloquants pour le reste de la phrase.

### Corrigé

- Le dossier `data/` est désormais ignoré par git.
