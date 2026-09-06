"""FastAPI 백엔드 — 프론트와 HTTP(JSON)로 통신하는 서버.

CLI(cli.py)를 대체하는 게 아니라, 같은 알맹이(search/assembly/...)를
HTTP로 노출하는 얇은 층이다. 로직은 기존 모듈을 그대로 재사용한다.

실행:
    py -m uvicorn notice_ai.api:app --reload --port 8000
확인:
    브라우저에서 http://localhost:8000/docs  ← API를 눌러볼 수 있는 자동 문서

환경변수는 기존과 동일(OPENSEARCH_ENDPOINT / USER / PASSWORD).
인증은 지금 단계에선 없음. 운영 전 접근 통제 추가 예정.
"""

from __future__ import annotations

from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel

from notice_ai.search import SearchHit, hybrid_search, search_notices

app = FastAPI(title="notice-draft-ai API", version="0.1")

# 프론트(브라우저)가 다른 포트/주소에서 호출할 수 있도록 CORS 허용.
# 지금은 로컬 개발용으로 전부 허용. 운영 시 실제 프론트 주소로 좁힌다.
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---- 응답 스키마 (프론트가 받게 될 JSON 형태) ----
class SearchHitOut(BaseModel):
    source_url: str
    title: str
    categories: list[str]
    published_at: str | None
    score: float
    snippet: str

    @classmethod
    def from_hit(cls, h: SearchHit) -> "SearchHitOut":
        return cls(
            source_url=h.source_url,
            title=h.title,
            categories=h.categories,
            published_at=h.published_at,
            score=h.score,
            snippet=h.snippet,
        )


class SearchResponse(BaseModel):
    query: str
    count: int
    hits: list[SearchHitOut]


@app.get("/health")
def health() -> dict:
    """서버가 살아있는지 확인용."""
    return {"status": "ok"}


@app.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., description="검색어"),
    category: str | None = Query(None, description="카테고리명(선택)"),
    hybrid: bool = Query(False, description="벡터 하이브리드 사용(임베딩 필요)"),
    rerank: bool = Query(False, description="리랭커 사용(Bedrock 필요)"),
    limit: int = Query(10, ge=1, le=50),
) -> SearchResponse:
    """유사 공지 검색 (기능 1).

    - hybrid=false: BM25(Nori)만. 임베딩/Bedrock 없이 바로 동작.
    - hybrid=true : BM25 + 벡터 RRF. 임베딩 없으면 자동으로 BM25만.
    """
    if hybrid:
        hits = hybrid_search(
            q,
            {"category": category} if category else {},
            use_rerank=rerank,
            category_name=category,
            limit=limit,
        )
    else:
        hits = search_notices(q, category_name=category, limit=limit)

    return SearchResponse(
        query=q,
        count=len(hits),
        hits=[SearchHitOut.from_hit(h) for h in hits],
    )


# ---- 초안 생성 (기능 2) ----
class DraftRequest(BaseModel):
    base_notice_url: str            # 사용자가 고른 기준 공지
    answers: dict                   # 문답으로 모은 값 (coin_kr, ticker, action, reason, datetime ...)
    category: str | None = None


class DraftResponse(BaseModel):
    draft: str
    warnings: list[str]
    base_url: str | None
    referenced: list[str]


@app.post("/draft", response_model=DraftResponse)
def draft(req: DraftRequest) -> DraftResponse:
    """기준 공지 + 문답값 → LLM 초안 생성 (기능 2).

    고위험 값(티커·날짜 등)은 프롬프트에 그대로 주입하고, 생성 후 반영됐는지 검증해
    warnings로 돌려준다. LLM은 LLM_PROVIDER 환경변수로 선택(openai/local/company).
    """
    from notice_ai.drafting import generate_draft

    r = generate_draft(req.base_notice_url, req.answers, category=req.category)
    return DraftResponse(
        draft=r.draft, warnings=r.warnings, base_url=r.base_url, referenced=r.referenced
    )
