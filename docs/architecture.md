# Architecture

ATLAS est un agent IA personnel, local-first. Le moteur est séparé des compétences, et le
modèle de langage est interchangeable. À ce stade, seul le squelette existe : les dossiers
ci-dessous décrivent le rôle prévu.

## Code (`src/atlas/`)

| Dossier ou fichier | Rôle |
| --- | --- |
| `config.py` | Réglages de l'application, lus depuis l'environnement et `.env`. |
| `core/` | Moteur : agent, vérificateur, trieur, reçus. Ne dépend d'aucune compétence. |
| `skills/` | Compétences, découvertes automatiquement. Une compétence vit dans `skills/<nom>/` avec `skill.py`, `manifest.yaml` et `tests/`. |
| `llm/` | Interface commune aux modèles (OpenRouter, Ollama). |
| `memory/` | Mémoire de l'agent. |
| `storage/` | Persistance locale des données. |
| `interfaces/` | Points d'entrée utilisateur ; aujourd'hui la CLI Typer (`cli.py`). |

## Hors du code

| Dossier | Rôle |
| --- | --- |
| `tests/` | Tests du projet ; aucun ne fait d'appel réseau (LLM simulé). |
| `docs/` | Documentation et décisions d'architecture (`adr/`). |
| `evals/` | Scénarios d'évaluation, au moins 3 par compétence. Créé avec la première compétence. |
| `legacy/` | Ancienne version, conservée comme référence. Ne jamais la modifier ni l'importer. |

## Règles de sécurité

- Tout outil déclare son `effect` (`None` signifie lecture seule).
- Les effets listés dans `requires_approval` ne s'exécutent qu'après validation humaine.
- Le vérificateur refuse toute affirmation d'action sans reçu d'outil réussi.
