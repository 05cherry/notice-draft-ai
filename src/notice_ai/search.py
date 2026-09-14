"""유사 공지 검색 — OpenSearch 하이브리드(BM25 Nori + kNN 벡터) + RRF + 리랭커.

흐름:
    query_text/filters ─┬─ bm25_search (Nori 형태소, 정확일치 강함)
                        └─ vector_search (kNN, 의미 유사 강함)  ※ 임베딩 켰을 때만
                        → RRF 융합 → (옵션) 리랭킹 → SearchHit[]

벡터 팔은 graceful degrade: Bedrock 미설정/임베딩 없음이면 조용히 BM25만 쓴다.
문서 id = source_url.
"""

from __future__ import annotations

from dataclasses import dataclass, replace

from notice_ai import config, fusion
from notice_ai.opensearch_client import get_client


@dataclass(frozen=True)
class SearchHit:
    source_url: str
    title: str
    categories: list[str]
    published_at: str | None
    score: float
    snippet: str = ""
    body: str = ""          # 본문(raw_text). 초안 참고용. /search 응답에는 넣지 않는다.
    tickers: tuple[str, ...] = ()


def _filter_clauses(filters: dict) -> list[dict]:
    clauses: list[dict] = []
    if filters.get("category"):
        # categories 배열에 이 값이 포함되면 매칭(대분류/소분류 구분 없이)
        clauses.append({"term": {"categories": filters["category"]}})
    for cat in filters.get("categories") or []:
        # 여러 개면 모두 포함한 공지만(AND). 예) 안내+입출금
        clauses.append({"term": {"categories": cat}})
    if filters.get("title_phrase"):
        # 제목에 이 구절이 있는 공지만. 예) '유의촉구' → 같은 유형 최신 공지 모으기
        clauses.append({"match_phrase": {"title": filters["title_phrase"]}})
    if filters.get("ticker"):
        clauses.append({"term": {"tickers": filters["ticker"]}})
    return clauses


def _hit(h: dict, score: float) -> SearchHit:
    s = h["_source"]
    hl = h.get("highlight", {}).get("raw_text", [])
    return SearchHit(
        source_url=s.get("source_url", h["_id"]),
        title=s.get("title", ""),
        categories=s.get("categories") or [],
        published_at=s.get("published_at"),
        score=score,
        snippet=(hl[0] if hl else ""),
        body=s.get("raw_text") or "",
        tickers=tuple(s.get("tickers") or ()),
    )


def bm25_search(
    query: str,
    filters: dict,
    size: int = 30,
    *,
    boost_phrases: tuple[str, ...] | list[str] = (),
    sort: str | None = None,
) -> list[SearchHit]:
    """BM25(Nori). boost_phrases: 제목에 이 구절이 있으면 가점(유형 맞추기).
    sort: None=관련도 / "score"=관련도, 동점이면 최신(비슷한 공지가 동점으로 많이 나온다)
          / "recent"=최신순(점수는 그대로 계산)."""
    body = {
        "size": size,
        "_source": {"excludes": ["embedding"]},
        "query": {
            "bool": {
                "must": [
                    # 분석기를 직접 지정하지 않는다: 필드의 search_analyzer(동의어 포함)가 쓰이게.
                    # search_analyzer가 없는 옛 인덱스(notices)에서는 필드 analyzer(korean)가 그대로 쓰인다.
                    {"multi_match": {
                        "query": query,
                        "fields": ["title^3", "raw_text"],
                    }}
                ],
                "filter": _filter_clauses(filters),
                "should": [{"match_phrase": {"title": {"query": p, "boost": 2.0}}} for p in boost_phrases],
            }
        },
        "highlight": {"fields": {"raw_text": {"fragment_size": 120, "number_of_fragments": 1}}},
    }
    recent = {"published_at": {"order": "desc", "missing": "_last"}}
    if sort in ("score", "recent"):
        body["sort"] = ["_score", recent] if sort == "score" else [recent, "_score"]
        body["track_scores"] = True   # sort를 주면 _score가 비는 것을 방지
    res = get_client().search(index=config.INDEX_NAME, body=body)
    return [_hit(h, h["_score"] or 0.0) for h in res["hits"]["hits"]]


def vector_search(qvec: list[float], filters: dict, size: int = 30) -> list[SearchHit]:
    knn = {"embedding": {"vector": qvec, "k": size}}
    fc = _filter_clauses(filters)
    if fc:
        knn["embedding"]["filter"] = {"bool": {"filter": fc}}
    body = {"size": size, "query": {"knn": knn}}
    res = get_client().search(index=config.INDEX_NAME, body=body)
    return [_hit(h, h["_score"]) for h in res["hits"]["hits"]]


def hybrid_search(
    query_text: str,
    filters: dict | None = None,
    *,
    use_hyde: bool = False,
    use_rerank: bool = False,
    category_name: str | None = None,
    candidate_k: int = 30,
    limit: int = 10,
) -> list[SearchHit]:
    filters = filters or {}
    kw = bm25_search(query_text, filters, candidate_k)

    vec: list[SearchHit] = []
    try:
        from notice_ai import embeddings

        embed_text = query_text
        if use_hyde and category_name:
            from notice_ai import hyde

            embed_text = hyde.hypothetical_notice(category_name, query_text)
        qvec = embeddings.embed_query(embed_text)
        vec = vector_search(qvec, filters, candidate_k)
    except Exception:
        vec = []  # 임베딩 미설정/실패 → BM25만

    by_id = {h.source_url: h for h in kw}
    by_id.update({h.source_url: h for h in vec})
    fused = fusion.rrf_fuse([[h.source_url for h in kw], [h.source_url for h in vec]])
    ordered = [i for i, _ in fused]

    if use_rerank and ordered:
        ordered = _rerank(query_text, ordered, by_id, limit)

    out: list[SearchHit] = []
    for rank, sid in enumerate(ordered[:limit]):
        base = by_id[sid]
        fs = next((s for i, s in fused if i == sid), 0.0)
        out.append(_rescore(base, fs if not use_rerank else 1.0 / (1 + rank)))
    return out


def _rerank(query_text, ordered, by_id, limit):
    from notice_ai import rerank as rr

    top = ordered[: max(limit * 2, 20)]
    docs = [f"{by_id[i].title}\n{by_id[i].snippet}" for i in top]
    try:
        return [top[idx] for idx, _ in rr.rerank(query_text, docs, top_n=limit)]
    except Exception:
        return ordered


def _rescore(h: SearchHit, score: float) -> SearchHit:
    return replace(h, score=score)


def search_notices(query: str, category_name: str | None = None, limit: int = 10) -> list[SearchHit]:
    """단순 검색(호환용). 필요시 hybrid_search 사용."""
    f = {"category": category_name} if category_name else {}
    return bm25_search(query, f, limit)[:limit]
