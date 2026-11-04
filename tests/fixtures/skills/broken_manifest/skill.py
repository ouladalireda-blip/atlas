"""Compétence de test dont le manifeste est invalide."""

from atlas.skills.base import Skill


class BrokenManifestSkill(Skill):
    name = "broken_manifest"
