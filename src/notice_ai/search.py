"""유사 공지 검색 — OpenSearch 하이브리드(BM25 Nori + kNN 벡터) + RRF + 리랭커.

흐름:
    query_text/filters ─┬─ bm25_search (Nori 형태소, 정확일치 강함)
                        └─ vector_search (kNN, 의미 유사 강함)  ※ 임베딩 켰을 때만
                        → RRF 융합 → (옵션) 리랭킹 → SearchHit[]

벡터 팔은 graceful degrade: Bedrock 미설정/임베딩 없음이면 조용히 BM25만 쓴다.
문서 id = source_url.
"""

from __future__ import annotations

import html
from dataclasses import dataclass, field, replace
from typing import Callable

from notice_ai import config, fusion, index_ref
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


_RECENT = {"published_at": {"order": "desc", "missing": "_last"}}


def _text_query(query: str) -> dict:
    # 분석기를 직접 지정하지 않는다: 필드의 search_analyzer(동의어 포함)가 쓰이게.
    # search_analyzer가 없는 옛 인덱스(notices)에서는 필드 analyzer(korean)가 그대로 쓰인다.
    return {"multi_match": {"query": query, "fields": ["title^3", "raw_text"]}}


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
                "must": [_text_query(query)],
                "filter": _filter_clauses(filters),
                "should": [{"match_phrase": {"title": {"query": p, "boost": 2.0}}} for p in boost_phrases],
            }
        },
        "highlight": {"fields": {"raw_text": {"fragment_size": 120, "number_of_fragments": 1}}},
    }
    if sort in ("score", "recent"):
        body["sort"] = ["_score", _RECENT] if sort == "score" else [_RECENT, "_score"]
        body["track_scores"] = True   # sort를 주면 _score가 비는 것을 방지
    res = get_client().search(index=index_ref.target(), body=body)
    return [_hit(h, h["_score"] or 0.0) for h in res["hits"]["hits"]]


def vector_search(qvec: list[float], filters: dict, size: int = 30) -> list[SearchHit]:
    knn = {"embedding": {"vector": qvec, "k": size}}
    fc = _filter_clauses(filters)
    if fc:
        knn["embedding"]["filter"] = {"bool": {"filter": fc}}
    body = {"size": size, "_source": {"excludes": ["embedding"]}, "query": {"knn": knn}}
    res = get_client().search(index=index_ref.target(), body=body)
    return [_hit(h, h["_score"]) for h in res["hits"]["hits"]]


def vector_similarity(qvec: list[float], ids: list[str]) -> dict[str, float]:
    """공지(id=source_url)별 질의 벡터와의 코사인 유사도. 둘 다 정규화돼 있어 내적이 곧 코사인.
    BM25로만 찾은 후보에도 의미 점수를 매기려고 쓴다. 임베딩 없는 공지는 결과에서 빠진다."""
    if not ids:
        return {}
    res = get_client().mget(index=index_ref.target(), body={"ids": list(ids)}, _source_includes=["embedding"])
    out = {}
    for d in res["docs"]:
        vec = (d.get("_source") or {}).get("embedding")
        if d.get("found") and vec:
            out[d["_id"]] = sum(a * b for a, b in zip(qvec, vec))
    return out


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

    by_id = {h.source_url: h for h in vec}
    by_id.update({h.source_url: h for h in kw})   # 둘 다 나오면 BM25 쪽(하이라이트 snippet 있음)을 쓴다
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


# ── 검색창(/search) ──────────────────────────────────────────────────────
# 1위 점수의 이 비율 미만은 뺀다. 사전에 없는 이름('헤데라')은 '헤'+'데' 같은 조각으로 쪼개지고 기본 검색은
# 조각 하나만 맞아도 결과에 넣어, 긴 꼬리 잡음이 붙고 건수가 부풀었다(헤데라 78→14건, 메가이더 4,229→4건, 전부 정답).
SEARCH_MIN_RATIO = 0.5
# 본 목록에 없는데 뜻이 비슷한 공지(의미 검색). 문장형 검색('빗썸 직원인 척 … 사기')은 BM25가 약하다.
# 벡터 유사도는 정답·오답 분포가 겹쳐 결과 건수를 정할 기준이 못 되므로 본 목록에는 섞지 않는다.
RELATED_K = 5
SEARCH_SORTS = {"relevance": ["_score", _RECENT], "recent": [_RECENT, "_score"]}
# OpenSearch는 from+size가 index.max_result_window(기본 1만)를 넘으면 오류를 낸다(쪽 번호가 너무 크면 500이 났음)
SEARCH_MAX_WINDOW = 10_000


@dataclass
class SearchPage:
    query: str
    total: int                  # 컷 이상 전체 결과 수(카테고리를 골랐으면 그 카테고리 안에서)
    page: int
    size: int
    sort: str
    min_score: float
    hits: list[SearchHit] = field(default_factory=list)
    categories: dict[str, int] = field(default_factory=dict)   # 카테고리별 결과 수(고른 카테고리와 무관)
    related: list[SearchHit] = field(default_factory=list)


def _snippet(h: dict) -> str:
    """HTML로 안전한 발췌(<em>만 들어 있다). 본문에 검색어가 없으면(제목만 맞음) 본문 앞부분."""
    hl = h.get("highlight", {}).get("raw_text")
    if hl:
        return hl[0].replace("</em><em>", "")   # 형태소 조각('헤'+'데')으로 나뉜 강조를 이어 붙인다
    return html.escape(" ".join((h["_source"].get("raw_text") or "").split())[:120])


def search_page(
    query: str,
    *,
    category: str | None = None,
    page: int = 1,
    size: int = 10,
    sort: str = "relevance",
    related: bool = True,
    embed: Callable | None = None,
) -> SearchPage:
    """검색창용. 관련도순(동점은 최신) 또는 최신순, 1위 점수 SEARCH_MIN_RATIO 미만 제외, 카테고리 필터·건수, 페이지.

    컷 기준(1위 점수)은 카테고리와 무관하게 전체에서 잡는다. 그래야 카테고리별 건수가 그 카테고리를 눌렀을 때의
    결과 수와 같다(카테고리는 post_filter라 집계에 영향 없음). related=True면 1쪽에 뜻이 비슷한 공지
    RELATED_K건을 붙인다 — 검색어가 여러 단어이거나 결과가 0건일 때만(Bedrock 실패 시 빈 목록).
    embed: 질의 임베딩 함수(테스트 주입용).
    """
    if sort not in SEARCH_SORTS:
        raise ValueError(f"sort는 {', '.join(SEARCH_SORTS)} 중 하나: {sort}")
    client = get_client()
    text = _text_query(query)
    first = client.search(index=index_ref.target(), body={"size": 1, "query": text, "_source": False})
    top = first["hits"]["hits"][0]["_score"] if first["hits"]["hits"] else 0.0
    out = SearchPage(query, 0, page, size, sort, round(top * SEARCH_MIN_RATIO, 4))
    cat_filter = _filter_clauses({"category": category} if category else {})

    if top > 0:
        body = {
            "from": (page - 1) * size, "size": size, "query": text, "min_score": out.min_score,
            "track_total_hits": True, "track_scores": True, "sort": SEARCH_SORTS[sort],
            "_source": {"excludes": ["embedding"]},
            "highlight": {"encoder": "html", "fields": {"raw_text": {"fragment_size": 120, "number_of_fragments": 1}}},
            "aggs": {"categories": {"terms": {"field": "categories", "size": 50}}},
        }
        if cat_filter:
            body["post_filter"] = {"bool": {"filter": cat_filter}}
        res = client.search(index=index_ref.target(), body=body)
        out.total = res["hits"]["total"]["value"]
        out.hits = [replace(_hit(h, h["_score"] or 0.0), snippet=_snippet(h)) for h in res["hits"]["hits"]]
        out.categories = {b["key"]: b["doc_count"] for b in res["aggregations"]["categories"]["buckets"]}

    # 한 단어(코인명·티커·키워드)는 BM25가 정확하고 의미 검색은 다른 코인 공지만 끌어와 잡음이 된다
    # ('헤데라' → 이오스트·에버스케일 공지). 여러 단어(문장)이거나 본 목록이 비었을 때만 붙인다.
    if related and page == 1 and (len(query.split()) >= 2 or out.total == 0):
        out.related = _related(query, category, {h.source_url for h in out.hits}, embed)
    return out


def _related(query: str, category: str | None, shown: set[str], embed: Callable | None) -> list[SearchHit]:
    """뜻이 가까운 공지 중 1쪽에 안 보이는 것 RELATED_K건. 본 목록 2쪽 이후에 있는 공지도 넣는다
    (문장형 검색은 정답이 BM25 뒤쪽에 묻히는 일이 많아, 본 목록 전체를 빼면 첫 화면에서 놓쳤다)."""
    try:
        if embed is None:
            from notice_ai.embeddings import embed_query as embed
        near = vector_search(embed(query), {"category": category} if category else {}, RELATED_K * 3)
    except Exception:
        return []
    fresh = [h for h in near if h.source_url not in shown][:RELATED_K]
    return [replace(h, snippet=html.escape(" ".join(h.body.split())[:120])) for h in fresh]
