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
