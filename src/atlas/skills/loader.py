"""Découverte et chargement des compétences présentes dans un dossier."""

import importlib.util
import inspect
import sys
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import yaml

from atlas.skills.base import Skill, SkillContext, ToolSpec
from atlas.skills.manifest import SkillManifest
from atlas.storage.migrator import MigrationSource

SKILLS_DIR = Path(__file__).parent
MANIFEST_FILE = "manifest.yaml"
MODULE_FILE = "skill.py"
MIGRATIONS_DIR = "migrations"


class SkillLoadError(Exception):
    """Une compétence ne respecte pas le contrat de chargement."""


@dataclass(frozen=True)
class SkillError:
    """Erreur de chargement, rattachée au dossier de la compétence."""

    folder: str
    message: str


@dataclass(frozen=True)
class LoadedSkill:
    """Compétence chargée avec son manifeste et ses outils."""

    manifest: SkillManifest
    skill: Skill
    tools: list[ToolSpec]
    # Dossier des migrations de la compétence, s'il existe.
    migrations: Path | None = None


@dataclass(frozen=True)
class SkillRegistry:
    """Résultat d'un chargement : compétences valides, outils et erreurs."""

    skills: list[LoadedSkill]
    tools: list[ToolSpec]
    errors: list[SkillError]

    def claim_vocabulary(self) -> dict[str, list[str]]:
        """Fusionne les vocabulaires d'affirmation de toutes les compétences, sans doublon."""
        vocabulary: dict[str, list[str]] = {}
        for loaded in self.skills:
            for effect, words in loaded.manifest.claims.items():
                vocabulary[effect] = list(dict.fromkeys([*vocabulary.get(effect, []), *words]))
        return vocabulary

    def migration_sources(self) -> list[MigrationSource]:
        """Retourne les migrations des compétences chargées, sans que le cœur les connaisse."""
        return [
            MigrationSource(loaded.manifest.name, loaded.migrations)
            for loaded in self.skills
            if loaded.migrations is not None
        ]

    def requires_approval(self) -> frozenset[str]:
        """Fusionne les effets soumis à validation humaine de toutes les compétences."""
        return frozenset(
            effect for loaded in self.skills for effect in loaded.manifest.requires_approval
        )


def _import_module(folder: Path) -> ModuleType:
    """Importe skill.py depuis son chemin, quel que soit l'emplacement du dossier."""
    module_name = f"atlas_skill_{folder.name}"
    spec = importlib.util.spec_from_file_location(module_name, folder / MODULE_FILE)
    if spec is None or spec.loader is None:
        raise SkillLoadError(f"Impossible d'importer {MODULE_FILE}.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except BaseException:
        del sys.modules[module_name]
        raise
    return module


def _find_skill_class(module: ModuleType) -> type[Skill]:
    """Retourne l'unique classe Skill définie dans le module."""
    classes = [
        candidate
        for _, candidate in inspect.getmembers(module, inspect.isclass)
        if issubclass(candidate, Skill) and candidate.__module__ == module.__name__
    ]
    if len(classes) != 1:
        raise SkillLoadError(f"{MODULE_FILE} doit définir une seule classe Skill ({len(classes)}).")
    return classes[0]


def _load_skill(folder: Path, context: SkillContext | None) -> LoadedSkill:
    """Charge une compétence ; toute anomalie remonte sous forme d'exception."""
    for filename in (MANIFEST_FILE, MODULE_FILE):
        if not (folder / filename).is_file():
            raise SkillLoadError(f"Fichier manquant : {filename}.")
    content = (folder / MANIFEST_FILE).read_text(encoding="utf-8")
    manifest = SkillManifest.model_validate(yaml.safe_load(content))
    skill_class = _find_skill_class(_import_module(folder))
    class_name = getattr(skill_class, "name", None)
    if class_name != manifest.name:
        raise SkillLoadError(
            f"Le manifeste indique {manifest.name!r}, mais la classe indique {class_name!r}."
        )
    skill = skill_class(context)
    tools = skill.tools()
    _check_declared_effects(manifest, tools)
    migrations = folder / MIGRATIONS_DIR
    return LoadedSkill(
        manifest=manifest,
        skill=skill,
        tools=tools,
        migrations=migrations if migrations.is_dir() else None,
    )


def _check_declared_effects(manifest: SkillManifest, tools: list[ToolSpec]) -> None:
    """Refuse un effet sans outil : une faute de frappe contournerait la validation humaine."""
    declared = {spec.effect for spec in tools}
    for field, effects in (
        ("requires_approval", manifest.requires_approval),
        ("claims", manifest.claims),
    ):
        unknown = sorted(set(effects) - declared)
        if unknown:
            raise SkillLoadError(
                f"{field} cite des effets qu'aucun outil ne déclare : {', '.join(unknown)}."
            )


def _check_unique_names(candidate: LoadedSkill, loaded: list[LoadedSkill]) -> None:
    """Refuse les doublons : un nom d'outil doit désigner une seule méthode."""
    if candidate.manifest.name in {item.manifest.name for item in loaded}:
        raise SkillLoadError(f"Nom de compétence déjà utilisé : {candidate.manifest.name}.")
    known_tools = {spec.name for item in loaded for spec in item.tools}
    duplicates = sorted(known_tools & {spec.name for spec in candidate.tools})
    if duplicates:
        raise SkillLoadError(f"Noms d'outils déjà utilisés : {', '.join(duplicates)}.")


def load_skills(
    skills_dir: Path = SKILLS_DIR, context: SkillContext | None = None
) -> SkillRegistry:
    """Charge chaque compétence ; une compétence cassée n'empêche pas les autres.

    Le contexte est transmis à chaque compétence à sa création.
    """
    loaded: list[LoadedSkill] = []
    errors: list[SkillError] = []
    for folder in sorted(path for path in skills_dir.iterdir() if path.is_dir()):
        if not (folder / MANIFEST_FILE).exists() and not (folder / MODULE_FILE).exists():
            continue
        try:
            candidate = _load_skill(folder, context)
            _check_unique_names(candidate, loaded)
        except Exception as error:
            errors.append(SkillError(folder.name, f"{type(error).__name__} : {error}"))
        else:
            loaded.append(candidate)
    tools = [spec for item in loaded for spec in item.tools]
    return SkillRegistry(skills=loaded, tools=tools, errors=errors)
