"""FastAPI 백엔드 — 프론트와 HTTP(JSON)로 통신하는 서버.

CLI(cli.py)를 대체하는 게 아니라, 같은 알맹이(search/assembly/...)를
HTTP로 노출하는 얇은 층이다. 로직은 기존 모듈을 그대로 재사용한다.

실행:
    py -m uvicorn notice_ai.api:app --reload --port 8000
확인:
    브라우저에서 http://localhost:8000/docs  ← API를 눌러볼 수 있는 자동 문서

환경변수는 기존과 동일(OPENSEARCH_ENDPOINT / USER / PASSWORD).
공개 인터넷에 올릴 때는 API_TOKEN(공유 토큰)·ALLOWED_ORIGINS(프론트 주소)를 준다 — auth.py 참고.
둘 다 없으면 로컬 개발처럼 아무 검사 없이 동작한다. 사용자별 인증은 아직 없다(#6).

외부 서비스 실패는 {"detail": 안내 문구}로 돌려준다(프론트는 detail을 그대로 보여 준다):
    LLM 호출 실패 502 / 제한시간 초과 504 / LLM 설정 없음 503 · 검색 서버 연결 실패 503 · 환경변수 없음 503.
처리 안 된 예외(500)는 CORS 헤더가 붙지 않아 브라우저에선 '연결 실패'로만 보이므로 여기서 잡는다.
"""

from __future__ import annotations

import asyncio
import json
import logging
from contextlib import asynccontextmanager
from pathlib import Path
from time import perf_counter
from typing import Literal

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import HTMLResponse, JSONResponse, StreamingResponse
from opensearchpy.exceptions import OpenSearchException
from pydantic import BaseModel, Field

from notice_ai import coins as coin_cache
from notice_ai.auth import install as install_auth
from notice_ai.config import ConfigError
from notice_ai.llm import LLMError
from notice_ai.search import SEARCH_MAX_WINDOW, SearchHit, search_page

logger = logging.getLogger(__name__)
_WEB = Path(__file__).resolve().parent / "web"


@asynccontextmanager
async def lifespan(_: FastAPI):
    """서버가 사는 동안 빗썸 거래 대상 목록을 10분마다 받아 둔다(notice_ai.coins).

    빗썸이 안 되더라도 서버는 그대로 뜬다 — 코인 이름 자동 채우기만 쉬고, 수기 입력은 계속 된다.
    """
    coin_cache.start()
    try:
        yield
    finally:
        await coin_cache.stop()


app = FastAPI(title="notice-draft-ai API", version="0.1", lifespan=lifespan)

# CORS(프론트가 다른 주소에서 부를 수 있게) + 공유 토큰 접근 통제.
# 둘 다 환경변수로만 켜진다 — ALLOWED_ORIGINS 없으면 전부 허용, API_TOKEN 없으면 검사 없음(로컬 그대로).
install_auth(app)

_LLM_STATUS = {"timeout": 504, "config": 503}   # 나머지(rate_limit·auth·error)는 502


@app.exception_handler(LLMError)
def _llm_failed(request: Request, exc: LLMError) -> JSONResponse:
    # 원래 예외(OpenAI가 준 상태코드·오류코드)까지 로그에 남긴다 — 응답 문구만으로는
    # 키 문제인지 모델 권한 문제인지 서버 로그에서 되짚을 수 없었다.
    logger.warning("LLM 실패(%s) %s: %s", exc.kind, request.url.path, exc, exc_info=exc.__cause__ or exc)
    return JSONResponse(status_code=_LLM_STATUS.get(exc.kind, 502),
                        content={"detail": f"초안 생성 AI 호출 실패 — {exc}", "kind": exc.kind})


@app.exception_handler(OpenSearchException)
def _search_down(request: Request, exc: OpenSearchException) -> JSONResponse:
    logger.warning("OpenSearch 실패 %s: %r", request.url.path, exc)
    return JSONResponse(status_code=503, content={
        "detail": f"검색 서버(OpenSearch)에 연결하지 못했습니다({type(exc).__name__}). 잠시 뒤 다시 시도하세요.",
        "kind": "search_unavailable"})


@app.exception_handler(ConfigError)
def _not_configured(request: Request, exc: ConfigError) -> JSONResponse:
    return JSONResponse(status_code=503, content={"detail": f"서버 설정이 빠졌습니다: {exc}", "kind": "config"})


# ---- 응답 스키마 (프론트가 받게 될 JSON 형태) ----
class SearchHitOut(BaseModel):
    source_url: str
    title: str
    categories: list[str]
    published_at: str | None
    score: float
    snippet: str                 # HTML로 안전한 발췌(검색어는 <em>으로 감쌈)

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
    total: int                   # 전체 결과 수(1위 점수 50% 이상, 카테고리를 골랐으면 그 안에서)
    count: int                   # 이번 페이지 건수
    page: int
    size: int
    sort: str                    # relevance | recent
    min_score: float             # 이 점수 미만은 결과에서 뺐다(1위 점수 × 0.5)
    categories: dict[str, int]   # 카테고리별 결과 수(필터 버튼용, 고른 카테고리와 무관)
    hits: list[SearchHitOut]
    related: list[SearchHitOut]  # 본 목록에 없지만 뜻이 비슷한 공지(의미 검색, 1쪽에만)


@app.get("/health")
def health(deep: bool = Query(False, description="검색 서버·임베딩·LLM 설정까지 확인(느림, LLM은 부르지 않음)")):
    """서버가 살아있는지 확인용. deep=true면 의존 서비스 상태(status: ok/degraded/down, down이면 503)."""
    if not deep:
        return {"status": "ok"}
    from notice_ai.health import check

    r = check()
    return JSONResponse(status_code=503 if r["status"] == "down" else 200, content=r)


@app.get("/search", response_model=SearchResponse)
def search(
    q: str = Query(..., description="검색어"),
    category: str | None = Query(None, description="카테고리명(선택)"),
    page: int = Query(1, ge=1, description="쪽 번호(1부터)"),
    size: int = Query(10, ge=1, le=50, description="쪽당 건수"),
    sort: Literal["relevance", "recent"] = Query("relevance", description="관련도순(동점은 최신) / 최신순"),
    related: bool = Query(True, description="1쪽에 뜻이 비슷한 공지(의미 검색) 붙이기. Bedrock 필요, 실패 시 빈 목록"),
    limit: int | None = Query(None, ge=1, le=50, description="(구) size"),
) -> SearchResponse:
    """공지 검색창 (기능 1).

    BM25(Nori)로 찾되 1위 점수의 50% 미만은 뺀다(사전에 없는 이름이 조각으로 쪼개져 붙는 긴 꼬리 제거).
    total·categories는 그 기준의 전체 건수. related는 단어가 안 겹쳐 본 목록에 없는 비슷한 공지.
    """
    q = q.strip()
    if not q:
        raise HTTPException(status_code=422, detail="검색어를 입력하세요.")
    size = limit or size
    if page * size > SEARCH_MAX_WINDOW:
        raise HTTPException(status_code=422,
                            detail=f"앞쪽 {SEARCH_MAX_WINDOW:,}건까지만 볼 수 있습니다. 쪽 번호를 줄이세요.")
    r = search_page(q, category=category, page=page, size=size, sort=sort, related=related)
    return SearchResponse(
        query=r.query, total=r.total, count=len(r.hits), page=r.page, size=r.size, sort=r.sort,
        min_score=r.min_score, categories=r.categories,
        hits=[SearchHitOut.from_hit(h) for h in r.hits],
        related=[SearchHitOut.from_hit(h) for h in r.related],
    )


@app.get("/ui", response_class=HTMLResponse, include_in_schema=False)
def ui() -> str:
    """간단한 공지 검색 화면(/search 호출). 사내 테스트용 — 정식 프론트는 별도."""
    return (_WEB / "search.html").read_text(encoding="utf-8")


# ---- 초안 생성 (기능 2) ----
# 흐름(프론트가 매번 전체 값을 보내는 무상태 방식):
#   GET /types → 카테고리(1~2개) 선택, subtype·질문 목록 확인
#   POST /extract → 요청문에서 입력값 뽑아 칸 미리 채우기 (선택, LLM 1회). 제안일 뿐 초안에 바로 쓰이지 않는다
#   POST /prepare → 유형 판별 + 누락 필드(질문) + 유사 공지 후보·자동 선택 (LLM 호출 없음)
#   POST /spellcheck → 초안의 맞춤법·띄어쓰기 제안 (LLM 1회). 본문을 고쳐 주지는 않는다
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
    hybrid: bool = Field(False, description="참고 공지 후보에 의미 검색(Bedrock 임베딩)을 섞기. "
                                            "유형이 정해진 요청은 BM25와 결과가 같고 general 문장형에서만 소폭 나음")

    def resolved_categories(self) -> list[str]:
        return self.categories or ([self.category] if self.category else [])

    def merged_inputs(self) -> dict:
        return {**self.answers, **self.inputs}


class DraftResponse(BaseModel):
    status: str                      # error | need_input | ready | ok | needs_review
    parts: list[dict]                # {category, subtype, label, matched, overridden, estimated}
    # 요청문 규칙으로 유형을 못 정해 비슷한 공지로 추정한 파트: {category, subtype, label, votes, k, neighbors[]}.
    # 화면에 '추정'으로 보여 주고, 사용자가 바꾸면 이후 요청에 subtypes로 보낸다.
    estimated_subtypes: list[dict] = []
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


def _run(req: DraftRequest, prepare_only: bool, on_progress=None) -> DraftResponse:
    from notice_ai.drafting import draft_notice

    o = draft_notice(
        req.resolved_categories(), text=req.text, inputs=req.merged_inputs(),
        part_inputs=req.part_inputs, subtypes=req.subtypes, base_notice_url=req.base_notice_url,
        evaluate_draft=req.evaluate, prepare_only=prepare_only, hybrid=req.hybrid,
        on_progress=on_progress,
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
    estimated_subtypes: list[dict] = []
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


class SpellRequest(CheckRequest):
    """/check 와 같은 몸통을 받는다. categories·inputs 가 있으면 그 사실값까지 지킨다."""


class SpellResponse(BaseModel):
    status: str                  # error | ok
    suggestions: list[dict]      # {before, after, reason, kind, positions} — 화면이 하나씩 적용
    protected: list[dict]        # 사실값을 건드리려다 막힌 제안 {before, after, reason, kind, guarded}
    dropped: int                 # 본문에 없는 곳을 고치라던 제안 수(지어낸 것)
    errors: list[str]
    warnings: list[str]


@app.post("/spellcheck", response_model=SpellResponse)
def spellcheck(req: SpellRequest) -> SpellResponse:
    """초안의 맞춤법·띄어쓰기·어색한 표현을 짚는다 (기능 3). LLM 1회.

    **고쳐진 본문을 돌려주지 않는다.** 위치가 딸린 제안만 준다. 맞춤법 검사가 본문을 고치면
    가상자산 이름·날짜·링크 같은 사실값이 조용히 바뀌고, factcheck 는 교정 뒤를 다시 보지
    않으므로 그대로 새어 나간다. 사실값과 겹치는 제안은 protected 로 내려보내 무엇을 막았는지
    알려 준다. 본문에 없는 곳을 고치라는(지어낸) 제안은 dropped 로 센다.

    적용 여부는 사람이 정하고, 고친 뒤에는 /check 로 다시 검사하면 된다.
    """
    from notice_ai.notice_types import resolve
    from notice_ai.spellcheck import check_spelling

    parts = None
    if req.resolved_categories():
        res = resolve(req.resolved_categories(), text=req.text, inputs=req.merged_inputs(),
                      part_inputs=req.part_inputs, subtypes=req.subtypes)
        parts = res.parts or None
    return SpellResponse(**check_spelling(
        req.draft_text(), title=req.title, body=req.body, parts=parts).to_dict())


@app.get("/types")
def types() -> list[dict]:
    """카테고리별 subtype과 필수/선택 입력(질문 문구 포함). 프론트 문답 폼용."""
    from notice_ai.notice_types import catalog

    return catalog()


class ExtractRequest(BaseModel):
    categories: list[str] = Field(default_factory=list, description="카테고리 1~2개. 예) ['입출금']")
    category: str | None = Field(None, description="(구) 단일 카테고리. categories가 없을 때만 사용")
    subtypes: dict[str, str] = Field(default_factory=dict, description="카테고리별 subtype 직접 지정(선택)")
    text: str = Field("", description="작성하려는 공지 요청문. 여기서 값을 뽑는다")

    def resolved_categories(self) -> list[str]:
        return self.categories or ([self.category] if self.category else [])


class ExtractResponse(BaseModel):
    status: str                  # error | ok
    parts: list[dict]
    fields: list[dict]           # 뽑은 값 {field, label, value, display, categories} — 화면 입력칸에 채운다
    rejected: list[dict]         # 형식이 안 맞아 버린 값 {field, label, value, problem}
    asked: list[str]             # 뽑아 보려 한 항목 이름(무엇을 못 찾았는지 알 수 있게)
    errors: list[str]
    warnings: list[str]


@app.post("/extract", response_model=ExtractResponse)
def extract(req: ExtractRequest) -> ExtractResponse:
    """요청문 → 입력값 제안 (기능 2의 앞단). LLM 1회.

    "헤데라(HBAR) 입출금 9/25 15시부터 중단, 네트워크 점검 때문" 같은 한 줄에서 coins·suspend_at·reason을
    뽑아 돌려준다. 화면은 이 값으로 입력칸을 채우고 '확인하세요'로 표시한다.

    뽑은 값은 **제안일 뿐이다.** /draft 는 사용자가 확인해 보낸 inputs 만 보므로, 여기서 틀려도
    초안의 사실값은 오염되지 않는다. 형식 검사를 통과 못 한 값은 fields가 아니라 rejected로 간다.
    """
    from notice_ai.extract import extract_inputs

    return ExtractResponse(**extract_inputs(
        req.resolved_categories(), text=req.text, subtypes=req.subtypes).to_dict())


class CoinOut(BaseModel):
    ticker: str                  # 대문자. 예) ETH
    name: str                    # 빗썸 한글명. 예) 이더리움
    english: str
    markets: list[str]           # 붙어 있는 마켓. 예) ["BTC", "KRW"]
    warning: bool                # 빗썸 유의 표시


class CoinsResponse(BaseModel):
    count: int                   # 이번 응답 건수
    total: int                   # 캐시가 갖고 있는 전체 건수
    updated_at: str | None       # 마지막으로 빗썸에서 받은 시각(UTC). 한 번도 못 받았으면 null
    stale: bool                  # 목록은 있으나 마지막 갱신이 실패함
    coins: list[CoinOut]


@app.get("/coins", response_model=CoinsResponse)
def coin_list(
    q: str = Query("", description="티커·한글명·영문명 일부. 비우면 전체"),
    limit: int = Query(500, ge=1, le=2000, description="최대 건수"),
    refresh: bool = Query(False, description="빗썸에서 지금 바로 다시 받기(신규 상장이 안 보일 때)"),
) -> CoinsResponse:
    """거래 대상 코인 목록 — 프론트의 코인 입력칸 자동완성용.

    10분마다 백그라운드로 받아 둔 메모리 캐시에서 바로 답한다(빗썸을 매번 부르지 않는다).
    캐시가 비어 있으면(방금 뜬 서버·첫 갱신 실패) 이번 요청에서 한 번 받아 채운다.
    """
    if refresh or not coin_cache.known():
        coin_cache.refresh()        # 동기 함수 = FastAPI가 스레드풀에서 부르므로 이벤트 루프를 막지 않는다
    s = coin_cache.status()
    found = coin_cache.search(q, limit=limit)
    return CoinsResponse(count=len(found), total=s["count"], updated_at=s["updated_at"],
                         stale=bool(s.get("stale")),
                         coins=[CoinOut(**c.as_dict()) for c in found])


@app.post("/prepare", response_model=DraftResponse)
def prepare(req: DraftRequest) -> DraftResponse:
    """유형 판별 + 누락 필드 + 유사 공지 후보(top-k)·자동 선택 이유. LLM 호출 없음.

    요청문 규칙으로 유형을 못 정하면 비슷한 공지들의 유형으로 추정해 적용하고 estimated_subtypes에 근거를 준다
    (parts[].estimated=true). 추정이 틀렸으면 subtypes로 바로잡아 다시 호출한다.
    missing_fields가 비고 후보를 확인했으면 같은 값(+원하면 base_notice_url)으로 /draft 호출.
    """
    return _run(req, prepare_only=True)


def _sse(event: str, data: dict) -> bytes:
    return f"event: {event}\ndata: {json.dumps(data, ensure_ascii=False)}\n\n".encode()


@app.post("/draft/stream")
async def draft_stream(req: DraftRequest) -> StreamingResponse:
    """/draft 와 같은 일을 하되 단계가 바뀔 때마다 알려 준다(Server-Sent Events).

    초안 만들기는 수십 초가 걸리는데 그동안 화면이 깜깜하다. 작업 ID를 주고 따로 조회하는
    방식도 있지만, 무료 플랜은 인스턴스가 하나뿐이고 15분이면 잠들었다 재시작하므로 들고 있던
    작업이 날아간다. 연결 하나로 밀면 저장할 상태가 없다.

    보내는 것:
        event: stage  {stage, label, slow, elapsed_ms, ...}   단계 시작
        event: done   DraftResponse + elapsed_ms(전체 걸린 시간)
        event: error  {detail, kind}

    done 에 elapsed_ms 를 넣는 이유: 각 단계가 걸린 시간은 '다음 단계가 온 시각 - 이 단계가
    온 시각'으로 구하는데, 마지막 단계 뒤에는 아무것도 오지 않는다. 흐름이 초안→검증→평가라
    마지막은 늘 평가이고, 그래서 정작 평가 시간만 영영 알 수 없었다. 끝난 시각이 있어야 채워진다.
    결과 모양은 /draft 와 같다. 값을 만드는 코드는 한 벌이고 여기서는 알리기만 한다.

    브라우저의 EventSource 는 헤더를 못 붙여 토큰을 낼 수 없다. 프론트는 fetch 로 읽는다.
    """
    queue: asyncio.Queue = asyncio.Queue()
    loop = asyncio.get_running_loop()
    started = perf_counter()

    def on_progress(stage: str, info: dict) -> None:
        # drafting 은 다른 스레드에서 돈다. 큐에 넣는 일만 이벤트 루프에 맡긴다.
        loop.call_soon_threadsafe(queue.put_nowait, ("stage", info))

    async def run() -> None:
        try:
            out = await asyncio.to_thread(_run, req, False, on_progress)
            await queue.put(("done", {**out.model_dump(),
                                      "elapsed_ms": int((perf_counter() - started) * 1000)}))
        except HTTPException as e:
            await queue.put(("error", {"detail": e.detail, "kind": "error"}))
        except LLMError as e:
            await queue.put(("error", {"detail": f"초안 생성 AI 호출 실패 — {e}", "kind": e.kind}))
        except OpenSearchException as e:
            await queue.put(("error", {"detail": f"검색 서버(OpenSearch)에 연결하지 못했습니다({type(e).__name__}).",
                                       "kind": "search_unavailable"}))
        except ConfigError as e:
            await queue.put(("error", {"detail": f"서버 설정이 빠졌습니다: {e}", "kind": "config"}))
        except Exception as e:      # 예외 처리기가 못 잡는다(응답이 이미 흐르고 있다)
            logger.exception("/draft/stream 실패")
            await queue.put(("error", {"detail": f"서버 오류가 났습니다({type(e).__name__}).", "kind": "error"}))
        finally:
            await queue.put(None)

    async def events():
        task = asyncio.create_task(run())
        try:
            while (item := await queue.get()) is not None:
                yield _sse(*item)
        finally:
            # 브라우저가 중간에 끊으면 여기로 온다. 이미 시작한 LLM 호출은 되돌릴 수 없지만
            # 매달린 작업을 남기지는 않는다.
            task.cancel()

    return StreamingResponse(events(), media_type="text/event-stream", headers={
        "Cache-Control": "no-cache",
        "X-Accel-Buffering": "no",      # 중간 프록시가 모아 두면 진행 표시가 한꺼번에 온다
    })


@app.post("/draft", response_model=DraftResponse)
def draft(req: DraftRequest) -> DraftResponse:
    """카테고리(1~2개) + 문답값 → 참고 공지 선택 → 초안 → 검증·평가 → (필요 시 1회 수정) → 최종 초안.

    필수값이 모자라면 LLM을 부르지 않고 status=need_input + missing_fields를 돌려준다.
    사실값(코인·날짜·링크·조항 등)이 입력과 어긋나면 final_check.issues에 error로 남는다.
    LLM은 LLM_PROVIDER 환경변수로 선택(openai/local/company).
    """
    return _run(req, prepare_only=False)
