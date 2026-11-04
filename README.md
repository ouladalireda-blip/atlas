# ATLAS

Agent IA personnel, local-first. Python 3.12, géré avec [uv](https://docs.astral.sh/uv/).

## Démarrage

```powershell
uv sync
Copy-Item .env.example .env
uv run atlas version
```

## Développement

```powershell
uv run pytest               # tests
uv run ruff check .         # style
uv run ruff format .        # formatage
uv run mypy src             # types
uv run pre-commit install   # active les vérifications avant chaque commit
```

## Documentation

- [Architecture](docs/architecture.md)
- [Décisions d'architecture](docs/adr/)
- [Journal des modifications](CHANGELOG.md)
