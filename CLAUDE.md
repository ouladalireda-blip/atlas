# ATLAS — conventions du projet

Agent IA personnel, local-first. Python 3.12, géré avec uv. Développé sous Windows.

## Architecture
- src/atlas/core : moteur (agent, vérificateur, trieur, reçus). Ne dépend d'AUCUNE compétence.
- src/atlas/skills/<nom> : une compétence = skill.py + manifest.yaml + tests/. Découverte automatique.
- Tout outil déclare son `effect` (None = lecture seule). Les effets listés dans
  `requires_approval` ne s'exécutent qu'après validation humaine.
- Le vérificateur refuse toute affirmation d'action sans reçu d'outil réussi.
- Le journal des reçus est la source de vérité : le modèle termine son tour par l'outil `respond`
  (message + numéros de reçus), fourni par le cœur (src/atlas/core/respond.py), jamais par une
  compétence. L'agent valide les numéros ; le détecteur de texte est un garde-fou secondaire.
- src/atlas/core/claims : détecteurs d'affirmations derrière le contrat `ClaimDetector`, choisis
  par ATLAS_CLAIM_DETECTOR (défaut `rules`). Panne ou dépassement de ATLAS_CLAIM_DETECTOR_TIMEOUT
  (défaut 1 s) : repli sur les règles, avec un avertissement journalisé ; après
  ATLAS_CLAIM_DETECTOR_MAX_TIMEOUTS dépassements (défaut 3), le détecteur est désactivé pour la
  session (repli permanent).
- src/atlas/llm : interface commune pour les modèles (OpenRouter, Ollama). Le modèle est interchangeable.

## Règles
- Typage complet, mypy strict sur src/. ruff pour lint et format.
- Toute nouvelle fonctionnalité arrive avec ses tests ; aucun test ne fait d'appel réseau
  (LLM simulé).
- Toute nouvelle compétence arrive avec au moins 3 scénarios dans evals/.
- Pas de secret dans le code : tout passe par .env (voir .env.example).
- Le dossier legacy/ est une ancienne version servant de référence : ne jamais le modifier
  ni l'importer.
- Commits au format Conventional Commits (feat:, fix:, docs:, test:, refactor:, chore:).
- Docstrings et commentaires en français, noms de code en anglais.
- Avancer par petites étapes : ne faire que ce qui est demandé, puis expliquer ce qui a été fait.

## Commandes (Windows, PowerShell)
- uv sync                : installe l'environnement
- uv run pytest          : lance les tests
- uv run ruff check .    : vérifie le style
- uv run ruff format .   : formate le code
- uv run mypy src        : vérifie les types
- uv run atlas           : lance ATLAS
- uv run atlas chat      : conversation avec l'agent (clé dans .env)
- uv run atlas stats     : taux de conformité (tours terminés par l'outil respond)
- uv run atlas tour N    : raconte le tour n° N depuis la base (appels au modèle, outils et reçus, réponses
  refusées et leur raison, verdict, tokens, coût) ; `atlas tour --dernier` pour le dernier
- ATLAS_LOG_LEVEL=DEBUG : journaux sur stderr ET dans data/logs/atlas.log (rotation simple) ;
  aucune clé ni valeur sensible du .env n'y figure (masquage, testé)
- uv run python evals/run_verifier_eval.py : mesure le vérificateur sur le jeu de développement
  (détection, fausses alertes, par effet, liste des erreurs)
- uv run python evals/run_verifier_eval.py --check : idem, échoue sous les planchers (evals/thresholds.yaml) ;
  un plancher ne s'applique qu'à une catégorie d'au moins 10 phrases (--min-sample N), sinon « INFORMATIF »
- uv run python evals/run_verifier_eval.py --test : mesure sur le jeu de test, chiffres agrégés seulement
- uv run python evals/run_verifier_eval.py --check --test : planchers du jeu de test (la CI ne le fait que sur les tags v*)
- uv run python evals/run_verifier_eval.py --test CHEMIN : mesure un jeu aveugle hors du dépôt, agrégats seulement
- uv run python evals/compare_detectors.py : compare des détecteurs sur le développement (détection,
  fausses alertes, par effet, effectifs, intervalles de Wilson 95 %, latence p50/p95) ;
  options : --detectors rules,..., --warmup N (tours de chauffe), --repeats N (mesures par phrase),
  --no-latency, --mlflow
- uv run python evals/compare_detectors.py --test : idem sur le test interne, agrégats seulement ;
  --test CHEMIN pour un jeu aveugle hors du dépôt
- uv run python evals/check_leakage.py : doublons exacts et quasi-doublons entre développement et
  test interne, en effectifs seulement ; --external CHEMIN ajoute un jeu hors du dépôt
- uv sync --group eval : installe MLflow (groupe hors CI ; un `uv sync` simple le retire)
- uv run mlflow ui --backend-store-uri sqlite:///mlruns/mlflow.db : consulte les runs MLflow
  (données dans mlruns/, ignorées par git)

## Règles de robustesse (à appliquer à tout nouveau code)
- Échouer bruyamment : toute configuration ou manifeste invalide bloque le chargement
  avec un message clair. Jamais d'erreur ignorée en silence.
- Sûr par défaut : un oubli ou une valeur manquante mène au comportement le plus sûr
  (ex. une action non déclarée n'est pas exécutée).
- Modes de défaillance : pour chaque composant, traiter et tester les cas
  absent / vide / invalide / lent / en échec.
- Aucune donnée personnelle ni secret ne doit pouvoir être commité (vérifier .gitignore).
- Une seule source de vérité : toute convention (noms d'effets, formats) est validée par le code.
- Mesurer avant d'optimiser : toute heuristique a un jeu d'évaluation dans evals/.
- En fin de tâche : lister les risques restants et les choix à valider par l'humain.

## Jeu de test du vérificateur (règle impérative)
- evals/verifier_test.yaml et evals/independent_cases.yaml (la source du découpage, qui contient
  aussi les phrases du test) ne peuvent être lus que par les scripts de mesure qui n'affichent que
  des agrégats : `run_verifier_eval.py --test`, `compare_detectors.py --test` et
  `check_leakage.py`. Jamais pour écrire, régler ou entraîner un détecteur.
- Ne pas ouvrir ces fichiers, ne pas en citer de phrase, ne pas s'en inspirer pour une règle, un mot
  de vocabulaire, un seuil ou un cas de développement. Pour développer, utiliser
  evals/verifier_cases.yaml et evals/verifier_dev.yaml.
- Ne jamais regénérer le jeu de test (split_cases.py refuse d'écraser) ni baisser un plancher de
  evals/thresholds.yaml pour faire passer la CI. Voir docs/adr/0004-jeu-de-test-cache.md.
