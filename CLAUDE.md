# ATLAS — conventions du projet

Agent IA personnel, local-first. Python 3.12, géré avec uv. Développé sous Windows.

## Architecture
- src/atlas/core : moteur (agent, vérificateur, trieur, reçus). Ne dépend d'AUCUNE compétence.
- src/atlas/skills/<nom> : une compétence = skill.py + manifest.yaml + tests/. Découverte automatique.
- Tout outil déclare son `effect` (None = lecture seule). Les effets listés dans
  `requires_approval` ne s'exécutent qu'après validation humaine.
- Le vérificateur refuse toute affirmation d'action sans reçu d'outil réussi.
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