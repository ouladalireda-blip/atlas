"""Embeddings 384 dimensions.

- `RemoteEmbedder` : n'importe quel endpoint compatible OpenAI (/v1/embeddings),
  sortie projetée à 384 dims pour rester compatible avec la colonne vector(384).
- `HashingEmbedder` : fallback local sans dépendance ni réseau (mots + trigrammes
  de caractères hachés). Moins « sémantique » qu'un vrai modèle, mais déterministe,
  instantané et suffisant pour les tests et le développement hors ligne.
"""
from __future__ import annotations

import hashlib
import re
import unicodedata

import httpx
import numpy as np

from ..config import settings

DIM = 384
_WORD = re.compile(r"[a-z0-9]+")
_STOP = {
    "le", "la", "les", "de", "des", "du", "un", "une", "et", "a", "au", "aux", "en", "est", "je", "tu", "il",
    "on", "que", "qui", "pour", "dans", "sur", "mon", "ma", "mes", "the", "an", "of", "to", "is", "in", "and",
    "my", "for", "on", "it", "ce", "ca", "pas", "ne", "me", "moi", "vous", "nous",
}


def _strip_accents(s: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", s) if not unicodedata.combining(c))


def tokens(text: str) -> list[str]:
    return [w for w in _WORD.findall(_strip_accents(text.lower())) if w not in _STOP and len(w) > 1]


def _h(s: str) -> tuple[int, float]:
    d = hashlib.blake2b(s.encode(), digest_size=8).digest()
    n = int.from_bytes(d, "little")
    return n % DIM, 1.0 if (n >> 63) & 1 else -1.0


class HashingEmbedder:
    def embed(self, text: str) -> np.ndarray:
        v = np.zeros(DIM, dtype=np.float32)
        for w in tokens(text):
            i, s = _h("w:" + w)
            v[i] += 2.0 * s
            padded = f"#{w}#"
            for j in range(len(padded) - 2):
                i, s = _h("c:" + padded[j : j + 3])
                v[i] += 0.5 * s
        n = np.linalg.norm(v)
        return v / n if n else v


class RemoteEmbedder:
    def __init__(self) -> None:
        rng = np.random.default_rng(42)
        self._proj: np.ndarray | None = None
        self._rng = rng

    def embed(self, text: str) -> np.ndarray:
        r = httpx.post(
            settings.embeddings_url,
            headers={"Authorization": f"Bearer {settings.embeddings_key}"},
            json={"model": settings.embeddings_model, "input": text},
            timeout=30,
        )
        r.raise_for_status()
        v = np.array(r.json()["data"][0]["embedding"], dtype=np.float32)
        if v.shape[0] != DIM:  # projection aléatoire fixe (Johnson-Lindenstrauss)
            if self._proj is None or self._proj.shape[1] != v.shape[0]:
                self._proj = self._rng.standard_normal((DIM, v.shape[0])).astype(np.float32)
            v = self._proj @ v
        n = np.linalg.norm(v)
        return v / n if n else v


def get_embedder():
    return RemoteEmbedder() if settings.embeddings_url else HashingEmbedder()
