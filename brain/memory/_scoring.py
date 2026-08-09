"""Shared recall scoring for MemoryStore backends.

Both stores (sqlite, postgres) blend the same three signals with the same
weights, so swapping the backend never changes what the brain recalls:

  semantic available:  0.55 * vector cosine + 0.35 * keyword + 0.10 * recency
  lexical only:        0.75 * keyword + 0.25 * recency

Keeping the math here means parity is structural, not copy-paste discipline.
"""

from __future__ import annotations

import math
import re
from typing import Sequence

# Blend weights — identical across backends by construction.
W_SEM_VEC, W_SEM_KW, W_SEM_REC = 0.55, 0.35, 0.10
W_LEX_KW, W_LEX_REC = 0.75, 0.25

# Recency half-life shaping: score = 1 / (1 + age_days / 30)
RECENCY_SCALE_DAYS = 30.0


def query_terms(query: str, *, min_length: int = 2) -> list[str]:
    """Normalize recall terms consistently across storage backends."""
    seen: set[str] = set()
    terms: list[str] = []
    for term in re.findall(r"[a-z0-9]+", str(query or "").lower()):
        if len(term) >= min_length and term not in seen:
            seen.add(term)
            terms.append(term)
    return terms


def cosine(a: Sequence[float], b: Sequence[float]) -> float:
    """Cosine similarity mapped to [0, 1]; 0.0 on any shape mismatch."""
    if not a or not b or len(a) != len(b):
        return 0.0
    dot = sum(x * y for x, y in zip(a, b))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(x * x for x in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return max(0.0, min(1.0, (dot / (na * nb) + 1.0) / 2.0))


def recency_score(created_at_epoch: float, now_epoch: float) -> float:
    age_days = max(0.0, now_epoch - created_at_epoch) / 86400.0
    return 1.0 / (1.0 + age_days / RECENCY_SCALE_DAYS)


def keyword_score(content: str, terms: Sequence[str]) -> float:
    if not terms:
        return 0.0
    text = content.lower()
    return sum(t in text for t in terms) / len(terms)


def blend(vec: float, kw: float, rec: float, *, semantic: bool) -> float:
    if semantic:
        return W_SEM_VEC * vec + W_SEM_KW * kw + W_SEM_REC * rec
    return W_LEX_KW * kw + W_LEX_REC * rec
