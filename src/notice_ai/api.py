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

from fastapi import FastAPI, HTTPException, Query
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field

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
# 흐름(프론트가 매번 전체 값을 보내는 무상태 방식):
#   GET /types → 카테고리(1~2개) 선택, subtype·질문 목록 확인
#   POST /prepare → 유형 판별 + 누락 필드(질문) + 유사 공지 후보·자동 선택 (LLM 호출 없음)
#   POST /draft → 초안 생성 + 검증 + 평가 + (필요 시 1회 수정) → 최종 초안
class DraftRequest(BaseModel):
    categories: list[str] = Field(default_factory=list, description="카테고리 1~2개. 예) ['안내','입출금']")
    category: str | None = Field(None, description="(구) 단일 카테고리. categories가 없을 때만 사용")
    subtypes: dict[str, str] = Field(default_factory=dict, description="카테고리별 subtype 직접 지정(선택)")
    text: str = Field("", description="작성하려는 공지 요청문(유형 판별·검색에 사용)")
    inputs: dict = Field(default_factory=dict, description="문답값(파트 공통). coins/suspend_at/reason ...")
    answers: dict = Field(default_factory=dict, description="(구) inputs 별칭. coin_kr/ticker/datetime 도 허용")
    part_inputs: dict[str, dict] = Field(default_factory=dict,
                                         description="카테고리별로만 다른 값. 예) {'거래지원종료': {'coins': [...]}}")
    base_notice_url: str | None = Field(None, description="사용자가 직접 고른 참고 공지(없으면 자동 선택)")
    evaluate: bool = Field(True, description="LLM 평가 포함(비용 발생). 코드 검증은 항상 수행")

    def resolved_categories(self) -> list[str]:
        return self.categories or ([self.category] if self.category else [])

    def merged_inputs(self) -> dict:
        return {**self.answers, **self.inputs}


class DraftResponse(BaseModel):
    status: str                      # error | need_input | ready | ok | needs_review
    parts: list[dict]
    missing_fields: list[dict]
    invalid_fields: list[dict]
    errors: list[str]
    warnings: list[str]
    query: str
    retrieval_note: str
    candidates: list[dict]
    selected_reference: dict | None
    selection_reason: str
    title_hint: str
    first_draft: str
    first_check: dict | None
    first_evaluation: dict | None
    revision_attempted: bool
    revision_reasons: list[str]
    revised: bool
    final_draft: str
    final_check: dict | None
    final_evaluation: dict | None
    needs_confirmation: list[str]
    # (구) 응답 필드 호환
    draft: str = ""
    base_url: str | None = None
    referenced: list[str] = []


def _run(req: DraftRequest, prepare_only: bool) -> DraftResponse:
    from notice_ai.drafting import draft_notice

    o = draft_notice(
        req.resolved_categories(), text=req.text, inputs=req.merged_inputs(),
        part_inputs=req.part_inputs, subtypes=req.subtypes, base_notice_url=req.base_notice_url,
        evaluate_draft=req.evaluate, prepare_only=prepare_only,
    )
    d = o.to_dict()
    sel = o.selected_reference or {}
    return DraftResponse(**d, draft=o.final_draft, base_url=sel.get("source_url"),
                         referenced=[c["source_url"] for c in o.candidates])


class NoticeOut(BaseModel):
    source_url: str
    title: str
    categories: list[str]
    published_at: str | None
    tickers: list[str]
    body: str                    # 수집한 원문 그대로
    original_title: str          # 초안이 참고할 때 쓰는 최초 버전(재개 등 업데이트 꼬리표 제거)
    original_body: str           # 본문 위에 덧붙은 업데이트 안내 제거
    update_removed: list[str]    # 무엇을 뺐는지(없으면 빈 목록)
    subtypes: dict[str, str]     # 카테고리별 판별 유형


class CheckRequest(DraftRequest):
    draft: str = Field("", description="검사할 초안 전체('제목:'/'본문:' 형식). title·body로 나눠 보내도 된다")
    title: str = Field("", description="초안 제목(draft 대신 사용)")
    body: str = Field("", description="초안 본문(draft 대신 사용)")

    def draft_text(self) -> str:
        return self.draft or f"제목: {self.title}\n본문:\n{self.body}"


class CheckResponse(BaseModel):
    status: str                  # error | ok | needs_review
    parts: list[dict]
    missing_fields: list[dict]
    invalid_fields: list[dict]
    errors: list[str]
    warnings: list[str]
    title: str = ""
    body: str = ""
    parsed: bool = False
    issues: list[dict] = []      # {code, severity(error|warn), message}
    needs_confirmation: list[str] = []


@app.get("/notice", response_model=NoticeOut)
def notice(url: str = Query(..., description="공지 source_url (/prepare·/draft 후보의 source_url)")) -> NoticeOut:
    """공지 1건 조회 — 참고 후보를 눌렀을 때 본문 미리보기용."""
    from notice_ai.drafting import get_notice_detail

    d = get_notice_detail(url)
    if d is None:
        raise HTTPException(status_code=404, detail=f"공지를 찾을 수 없습니다: {url}")
    return NoticeOut(**d)


@app.post("/check", response_model=CheckResponse)
def check(req: CheckRequest) -> CheckResponse:
    """사용자가 고친 초안을 다시 검사(코드 검사만, LLM 호출 없음·즉시).

    입력값(inputs 등)은 /draft 때와 같이 보낸다. 입력에 없는 날짜·코인·링크·조항, 입력값 누락,
    요일 오류, 남은 자리표시자 등을 issues로 돌려준다. base_notice_url을 주면 참고 공지의 코인 혼입도 본다.
    """
    from notice_ai.drafting import check_notice

    return CheckResponse(**check_notice(
        req.resolved_categories(), draft=req.draft_text(), text=req.text, inputs=req.merged_inputs(),
        part_inputs=req.part_inputs, subtypes=req.subtypes, base_notice_url=req.base_notice_url,
    ))


@app.get("/types")
def types() -> list[dict]:
    """카테고리별 subtype과 필수/선택 입력(질문 문구 포함). 프론트 문답 폼용."""
    from notice_ai.notice_types import catalog

    return catalog()


@app.post("/prepare", response_model=DraftResponse)
def prepare(req: DraftRequest) -> DraftResponse:
    """유형 판별 + 누락 필드 + 유사 공지 후보(top-k)·자동 선택 이유. LLM 호출 없음.

    missing_fields가 비고 후보를 확인했으면 같은 값(+원하면 base_notice_url)으로 /draft 호출.
    """
    return _run(req, prepare_only=True)


@app.post("/draft", response_model=DraftResponse)
def draft(req: DraftRequest) -> DraftResponse:
    """카테고리(1~2개) + 문답값 → 참고 공지 선택 → 초안 → 검증·평가 → (필요 시 1회 수정) → 최종 초안.

    필수값이 모자라면 LLM을 부르지 않고 status=need_input + missing_fields를 돌려준다.
    사실값(코인·날짜·링크·조항 등)이 입력과 어긋나면 final_check.issues에 error로 남는다.
    LLM은 LLM_PROVIDER 환경변수로 선택(openai/local/company).
    """
    return _run(req, prepare_only=False)
