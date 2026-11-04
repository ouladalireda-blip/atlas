# Architecture

ATLAS est un agent IA personnel, local-first. Le moteur est séparé des compétences, et le
modèle de langage est interchangeable. À ce stade, le contrat de compétence, les reçus,
l'exécuteur, le vérificateur, la boucle agent, les clients de modèle et la CLI sont implémentés : les autres dossiers ci-dessous
décrivent le rôle prévu.

## Code (`src/atlas/`)

| Dossier ou fichier | Rôle |
| --- | --- |
| `config.py` | Réglages de l'application, lus depuis l'environnement et `.env`. |
| `core/` | Moteur : agent, vérificateur, trieur, reçus, exécuteur, journal de consommation. Ne dépend d'aucune compétence : il n'utilise que le contrat de `skills/`. |
| `skills/` | Contrat (`base.py`, `manifest.py`, `loader.py`) et compétences, découvertes automatiquement. Une compétence vit dans `skills/<nom>/` avec `skill.py`, `manifest.yaml` et `tests/` ; voir [le guide](writing-a-skill.md). |
| `llm/` | Interface commune aux modèles : OpenRouter, Ollama, et `ScriptedLLM` pour les tests. Voir [l'ADR 0003](adr/0003-boucle-agent.md). |
| `memory/` | Mémoire de l'agent. |
| `storage/` | Persistance locale des données. |
| `interfaces/` | Points d'entrée utilisateur ; aujourd'hui la CLI Typer (`cli.py`). |

## Hors du code

| Dossier | Rôle |
| --- | --- |
| `tests/` | Tests du projet ; aucun ne fait d'appel réseau (LLM simulé). |
| `docs/` | Documentation et décisions d'architecture (`adr/`). |
| `evals/` | Scénarios d'évaluation, au moins 3 par compétence, et mesure du vérificateur (`run_verifier_eval.py`). |
| `legacy/` | Ancienne version, conservée comme référence. Ne jamais la modifier ni l'importer. |

## Règles de sécurité

- Tout outil déclare son `effect`, au format `domaine.action` (`None` signifie lecture seule).
- Les effets listés dans `requires_approval` ne s'exécutent qu'après validation humaine. La liste
  fusionne tous les manifestes et s'applique à tous les outils portant cet effet.
- Le vérificateur refuse toute affirmation d'action sans reçu d'outil réussi
  (voir [l'ADR 0002](adr/0002-verification-par-recus.md)).
- La boucle agent ne relance jamais un outil et ne laisse remonter aucune exception
  (voir [l'ADR 0003](adr/0003-boucle-agent.md)).
- Pas de donnée sensible via OpenRouter : utiliser Ollama pour ces cas.
