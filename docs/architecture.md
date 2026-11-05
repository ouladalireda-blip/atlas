# Architecture

ATLAS est un agent IA personnel, local-first. Le moteur est séparé des compétences, et le
modèle de langage est interchangeable. À ce stade, le contrat de compétence, les reçus,
l'exécuteur, le vérificateur, la boucle agent, les clients de modèle, le stockage SQLite et la CLI
sont implémentés : les autres dossiers ci-dessous décrivent le rôle prévu.

## Code (`src/atlas/`)

| Dossier ou fichier | Rôle |
| --- | --- |
| `config.py` | Réglages de l'application, lus depuis l'environnement et `.env`. |
| `core/` | Moteur : agent, vérificateur, trieur, reçus, exécuteur, journal de consommation. Ne dépend d'aucune compétence : il n'utilise que le contrat de `skills/`. |
| `logging_setup.py` | Journaux de l'espace `atlas` : fichier tournant `data/logs/atlas.log` (créé à la première ligne) et stderr (avertissements, tout en DEBUG), avec masquage des secrets (clé API, valeurs sensibles du `.env`, formes `sk-...`, `Bearer`). Les bibliothèques tierces ne sont pas capturées. |
| `core/trace.py` | Trace d'un tour (début, appels au modèle, reçus, refus avec leur raison, issue), gardée dans la table `turn_events` et lue par `atlas tour`. Elle ne contient jamais le message de l'utilisateur. |
| `core/respond.py` | L'outil `respond`, fourni par le cœur : le modèle termine son tour par un message et les numéros des reçus dont il rend compte. L'agent valide ces numéros contre le journal des reçus. Voir [l'ADR 0007](adr/0007-reponse-structuree.md). |
| `core/claims/` | Détecteurs d'affirmations d'action : le contrat `ClaimDetector`, R (`rules.py`, les règles), le repli sur R en cas de panne et la fabrique choisie par `ATLAS_CLAIM_DETECTOR`. Voir [l'ADR 0006](adr/0006-contrat-detecteurs-affirmations.md). |
| `skills/` | Contrat (`base.py`, `manifest.py`, `loader.py`) et compétences, découvertes automatiquement. Une compétence vit dans `skills/<nom>/` avec `skill.py`, `manifest.yaml` et `tests/` ; voir [le guide](writing-a-skill.md). |
| `llm/` | Interface commune aux modèles : OpenRouter, Ollama, et `ScriptedLLM` pour les tests. Voir [l'ADR 0003](adr/0003-boucle-agent.md). |
| `memory/` | Mémoire de l'agent. |
| `storage/` | Persistance SQLite : connexion (`database.py`), migrations numérotées (`migrator.py`, migrations du cœur dans `migrations/`), reçus (`receipts.py`) et consommation (`usage.py`). |
| `interfaces/` | Points d'entrée utilisateur ; aujourd'hui la CLI Typer (`cli.py`). |

## Stockage et migrations

- La base est un fichier SQLite (`ATLAS_DATABASE_PATH`, défaut `data/atlas.db`, dossier créé si
  absent, ignoré par git). Toutes les requêtes sont paramétrées (`?`), jamais construites par
  concaténation.
- Le cœur ouvre la base, applique les migrations, puis donne aux compétences un `SkillContext`
  (connexion et réglages). Une compétence n'ouvre jamais la base elle-même.
- Une migration est un fichier `NNNN_nom.sql`, numéroté à partir de 1 sans trou, appliqué dans sa
  propre transaction avec l'inscription de sa version dans `schema_migrations`. Le cœur a les
  siennes dans `storage/migrations/` ; une compétence place les siennes dans
  `skills/<nom>/migrations/`, et le cœur les découvre sans connaître la compétence.
- Chaque source (« core », puis chaque compétence) a sa propre numérotation. Une migration déjà
  appliquée ne doit jamais être modifiée (empreinte vérifiée) ; une base plus récente que le code
  est refusée.
- Reçus et consommation du modèle sont conservés dans SQLite (`SqliteReceiptStore`,
  `SqliteUsageStore`) ; les versions en mémoire servent aux tests.

## Validation humaine

Un outil dont l'effet figure dans `requires_approval` produit d'abord un reçu `pending_approval`.
Dans `atlas chat`, l'action et ses arguments sont affichés et « Approuver ? (o/n) » est posé ; la
réponse crée un nouveau reçu lié au premier : `ok` (ou `error`) après un oui, `rejected` après un
non. Toute autre saisie est redemandée, et une saisie interrompue vaut non. Le modèle reçoit le
résultat comme celui de n'importe quel outil.

## Hors du code

| Dossier | Rôle |
| --- | --- |
| `tests/` | Tests du projet ; aucun ne fait d'appel réseau (LLM simulé). |
| `docs/` | Documentation et décisions d'architecture (`adr/`). |
| `evals/` | Scénarios d'évaluation, au moins 3 par compétence, et mesure du vérificateur : jeu de développement et jeu de test séparés, planchers dans `thresholds.yaml` (voir [l'ADR 0004](adr/0004-jeu-de-test-cache.md)). `compare_detectors.py` compare les détecteurs (Wilson, latence, MLflow) et `check_leakage.py` contrôle les fuites entre jeux. |
| `legacy/` | Ancienne version, conservée comme référence. Ne jamais la modifier ni l'importer. |

## Règles de sécurité

- Tout outil déclare son `effect`, au format `domaine.action` (`None` signifie lecture seule).
- Les effets listés dans `requires_approval` ne s'exécutent qu'après validation humaine (voir ci-dessus). La liste
  fusionne tous les manifestes et s'applique à tous les outils portant cet effet.
- Le vérificateur refuse toute affirmation d'action sans reçu d'outil réussi
  (voir [l'ADR 0002](adr/0002-verification-par-recus.md)). Le cœur connaît les verbes
  (`core/lexicon.py`), les compétences leurs objets (`claims.nouns` et `claims.actions`) : l'effet
  d'une affirmation est la classe du verbe appliquée au domaine du nom le plus proche
  (voir [l'ADR 0005](adr/0005-verbes-et-objets.md) et le [format d'éval](eval-format.md)).
- Le journal des reçus est la source de vérité : le modèle termine par l'outil `respond` (message et
  numéros de reçus), l'agent valide les numéros, et `atlas chat` affiche les actions d'un tour depuis
  les reçus, séparées du message. Le détecteur de texte reste un garde-fou secondaire
  (voir [l'ADR 0007](adr/0007-reponse-structuree.md)). `atlas stats` donne le taux de conformité.
- La boucle agent ne relance jamais un outil et ne laisse remonter aucune exception
  (voir [l'ADR 0003](adr/0003-boucle-agent.md)).
- Pas de donnée sensible via OpenRouter : utiliser Ollama pour ces cas.
