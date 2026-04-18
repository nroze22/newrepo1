"""Semantic search over the clip library using OpenAI embeddings.

For each clip we store a 1536-dim float32 embedding of:
    "<title>\n<hook>\n<first 800 chars of transcript excerpt>"

Search computes cosine similarity in-memory. This is fast up to tens of
thousands of clips (numpy on a single process handles ~50k in <10ms).
For larger libraries, swap to sqlite-vec or FAISS — the interface stays
the same.
"""

from __future__ import annotations

import os
import struct
from dataclasses import dataclass
from typing import Iterable

import numpy as np

EMBEDDING_MODEL = "text-embedding-3-small"
EMBED_DIM = 1536
BATCH_SIZE = 64


def _client():
    from openai import OpenAI
    return OpenAI()


def _pack(vec: np.ndarray) -> bytes:
    v = np.asarray(vec, dtype=np.float32).reshape(-1)
    return v.tobytes()


def _unpack(blob: bytes) -> np.ndarray:
    return np.frombuffer(blob, dtype=np.float32)


def build_text_for_clip(*, title: str, hook: str, excerpt: str, archetype: str = "") -> str:
    bits = []
    if archetype:
        bits.append(f"Archetype: {archetype}")
    if title:
        bits.append(f"Title: {title}")
    if hook:
        bits.append(f"Hook: {hook}")
    if excerpt:
        bits.append(f"Transcript: {excerpt[:800]}")
    return "\n".join(bits) or "(empty)"


def embed_texts(texts: list[str]) -> list[np.ndarray]:
    """Batch-embed a list of texts. Requires OPENAI_API_KEY."""
    if not os.environ.get("OPENAI_API_KEY"):
        raise RuntimeError("OPENAI_API_KEY not set — required for semantic search.")
    client = _client()
    out: list[np.ndarray] = []
    for i in range(0, len(texts), BATCH_SIZE):
        chunk = texts[i:i + BATCH_SIZE]
        resp = client.embeddings.create(model=EMBEDDING_MODEL, input=chunk)
        for item in resp.data:
            out.append(np.asarray(item.embedding, dtype=np.float32))
    return out


def embed_one(text: str) -> np.ndarray:
    return embed_texts([text])[0]


def cosine_similarity(q: np.ndarray, mat: np.ndarray) -> np.ndarray:
    q = q / (np.linalg.norm(q) + 1e-9)
    norms = np.linalg.norm(mat, axis=1) + 1e-9
    return (mat @ q) / norms


@dataclass
class SearchHit:
    clip_id: int
    video_id: int
    score: float


def rank(query_vec: np.ndarray, items: list[tuple[int, int, np.ndarray]], *, top_k: int = 50) -> list[SearchHit]:
    """items = [(clip_id, video_id, vector), ...] — returns ranked hits."""
    if not items:
        return []
    mat = np.stack([v for _, _, v in items], axis=0)
    sims = cosine_similarity(query_vec, mat)
    idx = np.argsort(-sims)[:top_k]
    return [SearchHit(clip_id=items[i][0], video_id=items[i][1], score=float(sims[i])) for i in idx]
