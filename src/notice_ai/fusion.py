"""RRF (Reciprocal Rank Fusion) — 백엔드 무관, 재사용.

BM25와 벡터는 점수 척도가 달라 그냥 못 더한다. 순위만 써서 합친다.
    score(doc) = sum_lists  1 / (k + rank)
"""

from __future__ import annotations

from collections import defaultdict


def rrf_fuse(
    ranked_lists: list[list[str]],
    k: int = 60,
    weights: list[float] | None = None,
) -> list[tuple[str, float]]:
    """문서 id(문자열=source_url) 순위 리스트들을 합쳐 (id, score) 내림차순."""
    if weights is None:
        weights = [1.0] * len(ranked_lists)
    scores: dict[str, float] = defaultdict(float)
    for lst, w in zip(ranked_lists, weights):
        for rank, doc_id in enumerate(lst):
            scores[doc_id] += w * (1.0 / (k + rank))
    return sorted(scores.items(), key=lambda x: x[1], reverse=True)
