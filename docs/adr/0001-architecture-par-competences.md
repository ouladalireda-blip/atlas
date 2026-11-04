# ADR 0001 : architecture par compétences

- Statut : accepté
- Date : 2026-11-04

## Contexte

ATLAS doit gagner de nouvelles capacités au fil du temps sans fragiliser son moteur. Certaines
de ces capacités agissent sur le monde réel : il faut pouvoir les contrôler de la même façon,
quelle que soit la compétence qui les fournit.

## Décision

Le moteur (`src/atlas/core`) ne dépend d'aucune compétence. Chaque compétence vit dans
`src/atlas/skills/<nom>/` avec `skill.py`, `manifest.yaml` et `tests/`, et elle est découverte
automatiquement.

Tout outil déclare son `effect` (`None` pour la lecture seule). Les effets listés dans
`requires_approval` ne s'exécutent qu'après validation humaine, et le vérificateur refuse toute
affirmation d'action sans reçu d'outil réussi.

## Conséquences

- Ajouter une compétence ne demande aucune modification du moteur.
- Le contrôle des actions reste centralisé dans le moteur plutôt que réimplémenté par compétence.
- Le moteur doit définir un contrat stable pour les compétences et un mécanisme de découverte.
- Chaque compétence arrive avec ses tests et au moins 3 scénarios dans `evals/`.
