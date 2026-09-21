"""공지 초안 생성 파이프라인 (기능 2).

  입력(카테고리 1~2개 + 요청문 + 문답값)
   → 유형 판별·필수값 검증(notice_types)      부족하면 need_input 반환, LLM 호출 없음
     (규칙으로 못 정하면 뜻이 가까운 공지들의 유형으로 추정 → estimated_subtypes, 사용자 확인용)
   → 유사 공지 후보 검색(BM25 + 유형 구절 가점, 동점은 최신순 + 같은 유형 최신 공지 풀)
   → 참고 공지 선택(제목으로 판별한 유형 일치 > 검색점수·최신성, 본문 없는 공지 제외) 또는 사용자 지정
   → 프롬프트: 입력값(유일한 사실 출처) / 사실값을 가린 참고 공지(형식·문체 전용)
   → 초안 → 코드 검증(factcheck) + LLM 평가(evaluator)
   → 기준 미달이면 문제 목록을 넘겨 최대 1회 수정 → 최종 초안

원칙: 과거 공지는 형식·문체의 예시이지 사실 출처가 아니다. 사실값은 입력에서만 온다.
"""

from __future__ import annotations

import re
from collections import Counter, OrderedDict
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from pathlib import Path
from typing import Callable

from notice_ai import config, factcheck
from notice_ai.evaluator import Evaluation, evaluate
from notice_ai.llm import LLM, for_role
from notice_ai.notice_types import (
    FIELDS,
    Part,
    Resolution,
    classify_title,
    display_value,
    field_label,
    get_type,
    is_empty,
    now_kst,
    resolve,
    routing_text,
    title_hint,
    types_for,
)
from notice_ai.search import SearchHit, bm25_search

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_COMMON = (_PROMPT_DIR / "common.txt").read_text(encoding="utf-8")

MAX_REVISIONS = 1          # 무한 재시도 금지. 기준 미달이어도 수정은 1번만.
CANDIDATE_POOL = 50        # 동점이 많아 넉넉히 받고 코드로 재정렬
RECENT_POOL = 10           # 같은 유형 최신 공지는 관련도 순위와 별개로 이만큼 후보에 넣는다
MIN_BODY = 100             # 본문이 이보다 짧은 공지는 참고로 쓰지 않는다(예: 본문 없는 공시)
REF_CLIP = 2500            # 참고 공지 본문 길이 제한(토큰 절약)
GEN_MAX_TOKENS = 2500      # 1500이면 긴 공지(약관 개정 등)가 중간에 잘렸다
BOILERPLATE_REFS = 8       # '여러 공지에 반복되는 문장' 판정에 쓸 같은 유형 공지 수
STYLE_CLIP = 700
EVAL_REF_CLIP = 600
RECENCY_DAYS = 365 * 3     # 최신성: 오늘=1.0 → 3년 전=0.0 (선형)
# (검색점수, 최신성) 가중치. 같은 유형 후보끼리는 양식이 같아 최신 공지(최신 인사말·문구)가 중요하고,
# 주제가 제각각인 general은 내용 관련도가 더 중요하다.
W_TYPED = (0.5, 0.5)
W_GENERAL = (0.8, 0.2)
# 하이브리드(선택)일 때 검색점수 = α·BM25(후보 중 최댓값 대비) + (1-α)·의미 유사도(후보 안에서 0~1로 폄)
HYBRID_ALPHA = 0.5
# 유형 추정: 규칙 판별이 general이면 요청문과 뜻이 가까운 공지 K건의 유형 다수결(SHARE 이상)로 추정.
# 사용자 말투 요청 11개에서 규칙 2/11 → 10/11, 기존 라벨링 20/20·general 23/24 유지(tests/eval_hybrid.py [3]).
ESTIMATE_K = 5
ESTIMATE_SHARE = 0.6
ESTIMATE_MIN_TEXT = 8      # 요청문이 이보다 짧으면 추정하지 않는다(뜻을 가늠하기 어려움)

_TIER_TEXT = {
    0: "유형 일치",
    1: "유형 일치(다른 내용이 합쳐진 공지)",
    3: "같은 카테고리지만 유형 다름",
    4: "카테고리 불일치",
}
# 미입력 항목별로 LLM에 줄 구체 지시(실제로 틀렸던 사례 위주)
_MISSING_HINTS = {
    "resume_at": "제목에 '(MM/DD 재개)'를 붙이지 말고, 재개 시점은 '재개 시 별도 안내' 취지로 쓴다. 중지 날짜를 재개 날짜로 쓰지 않는다.",
    "deposit_status": "'현재 입금(입출금)이 중단된 상태' 같은 상태 서술과 입금 중지 시점을 쓰지 않는다.",
    "network": "대상 네트워크 자리에 [네트워크 확인 필요].",
    "law_clause": "조항 번호 자리에 [조항 확인 필요].",
    "next_review": "지정 연장·해제 / 거래지원 종료 공지 일정 자리에 [확인 필요].",
    "links": "링크(URL)를 만들어 넣지 않는다.",
    "deposit_resume_at": "입금 재개 시점 자리에 [확인 필요].",
    "paid_at": "지급 일정을 지어내지 말고 '추후 공지 예정' 취지로 쓴다.",
    "withdraw_open": "출금 오픈 일정을 지어내지 말고 '추후 공지 예정' 취지로 쓴다.",
}


# ── 프롬프트 파일 ────────────────────────────────────────────────────────
def _system_prompt(parts: list[Part]) -> str:
    """공통 지침 + 카테고리별 지침 + subtype 지침(있으면)을 순서대로 얹는다."""
    chunks = [_COMMON]
    for p in parts:
        for f in (_PROMPT_DIR / "category" / f"{p.category}.txt",
                  _PROMPT_DIR / "subtype" / p.category / f"{p.ntype.subtype}.txt"):
            if f.exists():
                txt = f.read_text(encoding="utf-8")
                if txt not in chunks:
                    chunks.append(txt)
    return "\n\n".join(chunks)


# ── 후보 검색·선택 ───────────────────────────────────────────────────────
@dataclass
class Candidate:
    hit: SearchHit
    subtypes: dict[str, str]    # 제목으로 판별한 {카테고리: subtype}
    tier: int = 4
    recency: float = 0.0
    rank_score: float = 0.0
    relevance: float = 0.0      # 0~1 검색점수(BM25, 하이브리드면 의미 유사도 섞음)

    def brief(self, rank: int | None = None) -> dict:
        h = self.hit
        return {"rank": rank, "source_url": h.source_url, "title": h.title, "categories": h.categories,
                "published_at": h.published_at, "score": round(h.score, 3), "subtypes": self.subtypes,
                "tier": self.tier, "match": _TIER_TEXT[self.tier], "recency": round(self.recency, 3),
                "relevance": round(self.relevance, 3), "rank_score": round(self.rank_score, 3)}


def _coin_tokens(parts: list[Part]) -> set[str]:
    toks = set()
    for p in parts:
        for name, v in p.inputs.items():
            if name in FIELDS and FIELDS[name].kind == "coins" and isinstance(v, list):
                toks.update(t for c in v for t in (c.get("name"), c.get("ticker")) if t and len(t) >= 2)
    return toks


def build_query(parts: list[Part], text: str = "") -> str:
    """검색어 = 유형 대표 표현 + 사유/주제 + 요청문. 새 코인명은 뺀다
    (같은 코인의 옛 공지가 끌려오면 그 공지의 네트워크·일정이 새 공지로 새기 쉽다)."""
    chunks = [p.ntype.query for p in parts]
    for p in parts:
        for name in ("reason", "topic", "details", "document", "service", "targets", "changes"):
            v = p.inputs.get(name)
            if isinstance(v, str) and v:
                chunks.append(v[:80])
    chunks.append(text or "")
    q = " ".join(dict.fromkeys(c for c in chunks if c))
    for tok in sorted(_coin_tokens(parts), key=len, reverse=True):
        q = q.replace(tok, " ")
    q = re.sub(r"\(\s*\)", " ", q)
    q = re.sub(r"\s+", " ", q).strip()
    return q or " ".join(p.ntype.label for p in parts)


def find_candidates(
    parts: list[Part], query: str, *, search: Callable = bm25_search, semantic: Callable | None = None,
) -> tuple[list[SearchHit], str]:
    """선택한 카테고리를 모두 가진 공지를 찾는다. 2개를 골랐으면 1차 카테고리 공지도 합친다
    (같은 '유의촉구 및 입출금 일시 중단' 공지인데 [안내]만 달린 것도 있어 태그만 믿을 수 없다).
    semantic(filters, size)를 주면 의미 검색(kNN) 결과도 합친다(하이브리드, 선택)."""
    cats = [p.category for p in parts]
    boost = tuple(b for p in parts for b in p.ntype.title_boost)
    hits = search(query, {"categories": cats}, CANDIDATE_POOL, boost_phrases=boost, sort="score")
    note = f"카테고리 {'+'.join(cats)}를 모두 가진 공지 {len(hits)}건"
    seen = {h.source_url for h in hits}

    def merge(more: list[SearchHit]) -> int:
        new = [h for h in more if h.source_url not in seen]
        seen.update(h.source_url for h in new)
        hits.extend(new)
        return len(new)

    if len(cats) > 1:
        n = merge(search(query, {"categories": [cats[0]]}, CANDIDATE_POOL, boost_phrases=boost, sort="score"))
        note += f" + 1차 카테고리 '{cats[0]}' 공지 {n}건(유형은 제목으로 판별)"
    # 관련도 상위가 옛 양식 제목으로만 채워지는 경우가 있어(예: '…업그레이드로 인한…'),
    # 같은 유형 구절이 제목에 있는 최신 공지를 따로 넣는다(같은 검색어라 점수 척도는 같다).
    phrase = parts[0].ntype.title_boost[0] if parts[0].ntype.title_boost else ""
    if phrase:
        n = merge(search(query, {"categories": [cats[0]], "title_phrase": phrase}, RECENT_POOL,
                         boost_phrases=boost, sort="recent"))
        note += f" + 제목에 '{phrase}'가 있는 최신 공지 {n}건"
    if semantic is not None:
        # 단어가 안 겹쳐도 뜻이 비슷한 공지. BM25 상위에 없던 공지라 BM25 점수는 0으로 둔다
        try:
            n = merge([replace(h, score=0.0) for h in semantic({"categories": cats}, CANDIDATE_POOL)])
            note += f" + 의미 검색으로만 찾은 공지 {n}건"
        except Exception:
            note += " (의미 검색 실패 → BM25만)"
    return hits, note


def _age_days(published_at: str | None, now: datetime) -> float:
    try:
        return max(0.0, (now - datetime.strptime((published_at or "")[:10], "%Y-%m-%d")).days)
    except ValueError:
        return float(RECENCY_DAYS)


def _candidate(h: SearchHit, parts: list[Part], now: datetime, relevance: float) -> Candidate:
    """등급: 0 유형 일치 / 1 유형 일치지만 다른 내용이 합쳐진 공지 / 3 유형 다름 / 4 카테고리 불일치.
    유형은 카테고리 태그가 아니라 제목으로 판별한다(태그가 일부만 붙은 공지가 있어서)."""
    cats = [p.category for p in parts]
    want = {p.category: p.ntype.subtype for p in parts}
    # 본문도 함께 본다(에어드랍 완료/예정처럼 제목만으론 안 갈리는 유형). 재개 안내 등은 떼고 최초 본문으로.
    subs = classify_title(h.title, cats, body=factcheck.original_version(h.title, h.body)[1][:1500])
    type_ok = subs == want
    # 선택 안 한 카테고리가 붙었거나, 단일 유형 요청인데 '… 및 …'로 합쳐진 공지는 한 단계 뒤로
    extra = bool(set(h.categories) - set(cats)) or (len(cats) == 1 and " 및 " in h.title)
    if type_ok:
        tier = 1 if extra else 0
    elif cats[0] in h.categories:
        tier = 3
    else:
        tier = 4
    rec = max(0.0, 1 - _age_days(h.published_at, now) / RECENCY_DAYS)
    w_score, w_rec = W_GENERAL if all(p.ntype.subtype == "general" for p in parts) else W_TYPED
    return Candidate(h, subs, tier, rec, w_score * relevance + w_rec * rec, relevance)


def _relevance(hits: list[SearchHit], sims: dict[str, float] | None) -> dict[str, float]:
    """후보별 검색점수(0~1). BM25는 최댓값 대비. sims가 있으면 의미 유사도를 후보 안 최소~최대로
    0~1로 펴서 HYBRID_ALPHA로 섞는다(Titan 코사인은 0.3~0.6에 몰려 있어 그대로 쓰면 차이가 안 난다)."""
    max_score = max((h.score for h in hits), default=0.0) or 1.0
    rel = {h.source_url: h.score / max_score for h in hits}
    if sims is None:
        return rel
    vals = [sims[u] for u in rel if u in sims]
    lo, hi = (min(vals), max(vals)) if vals else (0.0, 1.0)
    span = (hi - lo) or 1.0
    return {u: HYBRID_ALPHA * b + (1 - HYBRID_ALPHA) * ((sims[u] - lo) / span if u in sims else 0.0)
            for u, b in rel.items()}


def semantic_hooks(query: str) -> tuple[Callable, Callable]:
    """하이브리드용 (kNN 검색 함수, 후보별 의미 유사도 함수). 질의 임베딩(Bedrock 호출)은 한 번만."""
    from notice_ai.embeddings import embed_query
    from notice_ai.search import vector_search, vector_similarity

    qvec = embed_query(query)
    return (lambda filters, size: vector_search(qvec, filters, size)), (lambda ids: vector_similarity(qvec, ids))


# ── 유형 추정(규칙으로 못 정한 요청문) ──────────────────────────────────────
def semantic_neighbors(category: str, text: str, k: int) -> list[SearchHit]:
    """요청문과 뜻이 가까운 이 카테고리 공지 k건(Bedrock 임베딩 + kNN)."""
    from notice_ai.embeddings import embed_query
    from notice_ai.search import vector_search

    return vector_search(embed_query(text), {"categories": [category]}, k)


def estimate_subtypes(res: Resolution, text: str, inputs: dict | None) -> tuple[dict[str, str], list[dict], list[str]]:
    """규칙 판별이 general이고 사용자가 유형을 지정하지 않은 파트만, 뜻이 가까운 공지 K건의 유형 다수결로 추정.
    '출금이 늦게 처리돼요'(→ 지연), '유의 종목 기간을 늘리려고요'(→ 연장)처럼 규칙 표현이 없는 말투용.
    반환: ({카테고리: 추정 subtype}, 근거 목록, 경고). 임베딩·검색이 실패하면 추정 없이 규칙 결과를 쓴다."""
    q = routing_text(text, inputs).strip()
    est: dict[str, str] = {}
    info: list[dict] = []
    warnings: list[str] = []
    for p in res.parts:
        if p.ntype.subtype != "general" or p.overridden or len(types_for(p.category)) < 2 or len(q) < ESTIMATE_MIN_TEXT:
            continue
        try:
            hits = semantic_neighbors(p.category, q, ESTIMATE_K)
        except Exception as e:
            warnings.append(f"'{p.category}' 유형 추정(의미 검색)에 실패해 일반 유형으로 진행합니다: {str(e)[:80]}")
            continue
        subs = [classify_title(h.title, [p.category],
                               body=factcheck.original_version(h.title, h.body)[1][:1500])[p.category] for h in hits]
        if not subs:
            continue
        top, votes = Counter(subs).most_common(1)[0]
        if top == "general" or votes / ESTIMATE_K < ESTIMATE_SHARE:
            continue
        label = get_type(p.category, top).label
        est[p.category] = top
        info.append({"category": p.category, "subtype": top, "label": label, "votes": votes, "k": len(hits),
                     "neighbors": [{"title": h.title, "subtype": s, "source_url": h.source_url,
                                    "published_at": h.published_at} for h, s in zip(hits, subs)]})
        warnings.append(f"'{p.category}' 유형을 요청문 규칙으로 정하지 못해, 비슷한 공지 {len(hits)}건 중 {votes}건을 "
                        f"근거로 '{label}'(으)로 추정했습니다. 다르면 유형을 직접 지정하세요(subtypes).")
    return est, info, warnings


def resolve_with_estimate(
    categories: list[str], *, text: str = "", inputs: dict | None = None,
    part_inputs: dict[str, dict] | None = None, subtypes: dict[str, str] | None = None,
) -> tuple[Resolution, list[dict]]:
    """resolve + 유형 추정. 추정되면 그 유형으로 다시 풀어(필수 질문도 그 유형 기준) 추정 표시를 단다."""
    res = resolve(categories, text=text, inputs=inputs, part_inputs=part_inputs, subtypes=subtypes)
    if res.errors:
        return res, []
    est, info, warnings = estimate_subtypes(res, text, inputs)
    if est:
        res = resolve(categories, text=text, inputs=inputs, part_inputs=part_inputs, subtypes=subtypes, estimated=est)
    res.warnings.extend(warnings)
    return res, info


def rank_candidates(
    hits: list[SearchHit], parts: list[Part], *, now: datetime, sims: dict[str, float] | None = None,
) -> list[Candidate]:
    """본문 없는 공지 제외 → (tier 오름차순, 검색점수·최신성 가중합 내림차순).
    sims(공지 URL → 질의와의 의미 유사도)를 주면 검색점수에 의미 유사도를 섞는다(하이브리드)."""
    usable = [h for h in hits if len(h.body.strip()) >= MIN_BODY]
    rel = _relevance(usable, sims)
    ranked = [_candidate(h, parts, now, rel[h.source_url]) for h in usable]
    ranked.sort(key=lambda c: (c.tier, -c.rank_score))
    return ranked


def select_reference(ranked: list[Candidate]) -> tuple[Candidate | None, str]:
    if not ranked:
        return None, "참고할 공지를 찾지 못했습니다(본문 있는 후보 없음)."
    top = ranked[0]
    reason = (f"{_TIER_TEXT[top.tier]} · 후보 {len(ranked)}건 중 1위"
              f"(검색점수 {top.hit.score:.1f}, 최신성 {top.recency:.2f}) · {(top.hit.published_at or '')[:10]} 게시")
    if top.tier >= 3:
        reason += " · 같은 유형의 공지가 없어 가장 가까운 공지를 골랐습니다(형식 신뢰도 낮음)"
    return top, reason


def _style_example(ranked: list[Candidate], selected: Candidate) -> Candidate | None:
    """선택 공지보다 더 최신인 같은 등급 공지 1건(인사말·끝인사 최신 표현용)."""
    newer = [c for c in ranked if c is not selected and c.tier == selected.tier
             and (c.hit.published_at or "") > (selected.hit.published_at or "")]
    return max(newer, key=lambda c: c.hit.published_at or "", default=None)


def _fetch_notice(source_url: str) -> dict | None:
    """공지 1건. 없으면 None, 검색 서버 연결 오류 등은 그대로 올린다(/notice가 503으로 알림)."""
    from opensearchpy.exceptions import NotFoundError

    from notice_ai.opensearch_client import get_client

    try:
        return get_client().get(index=config.INDEX_NAME, id=source_url)["_source"]
    except NotFoundError:
        return None


def _get_notice(source_url: str) -> dict | None:
    """초안 흐름용: 어떤 이유로든 못 가져오면 None(호출한 쪽이 경고·오류 문구로 처리)."""
    try:
        return _fetch_notice(source_url)
    except Exception:
        return None


def get_notice_detail(url: str) -> dict | None:
    """공지 1건: 원문 + 초안이 참고할 때 쓰는 최초 버전(업데이트 제외) + 제목·본문으로 판별한 유형.
    없으면 None. 검색 서버에 못 붙으면 예외(→ 503)."""
    doc = _fetch_notice(url)
    if not doc:
        return None
    title, body = doc.get("title", ""), doc.get("raw_text") or ""
    orig_title, orig_body, removed = factcheck.original_version(title, body)
    cats = doc.get("categories") or []
    return {"source_url": doc.get("source_url", url), "title": title, "categories": cats,
            "published_at": doc.get("published_at"), "tickers": list(doc.get("tickers") or []), "body": body,
            "original_title": orig_title, "original_body": orig_body, "update_removed": removed,
            "subtypes": classify_title(title, cats, body=orig_body[:1500])}


def check_notice(
    categories: list[str],
    *,
    draft: str,
    text: str = "",
    inputs: dict | None = None,
    part_inputs: dict[str, dict] | None = None,
    subtypes: dict[str, str] | None = None,
    base_notice_url: str | None = None,
) -> dict:
    """사용자가 고친 초안을 코드 검사로만 다시 확인한다(LLM 호출 없음).
    참고 공지를 주면 그 공지의 가상자산이 섞였는지도 본다(고유 문장 복사 검사는 /draft에서만)."""
    res, estimated = resolve_with_estimate(
        categories, text=text, inputs=inputs, part_inputs=part_inputs, subtypes=subtypes)
    out = {"status": "error", "parts": [p.to_dict() for p in res.parts], "estimated_subtypes": estimated,
           "missing_fields": res.missing, "invalid_fields": res.invalid, "errors": list(res.errors),
           "warnings": list(res.warnings)}
    if res.errors:
        return out
    ref_tickers: set[str] = set()
    if base_notice_url:
        doc = _get_notice(base_notice_url)
        if doc:
            ref_tickers = set(doc.get("tickers") or ()) | factcheck.paren_tickers(
                f"{doc.get('title', '')}\n{doc.get('raw_text') or ''}")
        else:
            out["warnings"].append("참고 공지를 찾을 수 없어 참고 공지 가상자산 혼입 검사는 건너뜁니다.")
    chk = factcheck.check_draft(draft, res.parts, reference_tickers=ref_tickers)
    out.update(chk.to_dict())
    out["status"] = "needs_review" if chk.errors else "ok"
    return out


def _user_selected(url, ranked, parts, now, get_notice) -> tuple[Candidate | None, str]:
    for c in ranked:
        if c.hit.source_url == url:
            return c, f"사용자가 직접 고른 참고 공지 · {_TIER_TEXT[c.tier]}"
    doc = (get_notice or _get_notice)(url)
    if not doc:
        return None, f"참고 공지를 찾을 수 없습니다: {url}"
    hit = SearchHit(source_url=doc.get("source_url", url), title=doc.get("title", ""),
                    categories=doc.get("categories") or [], published_at=doc.get("published_at"),
                    score=0.0, body=doc.get("raw_text") or "", tickers=tuple(doc.get("tickers") or ()))
    c = _candidate(hit, parts, now, 0.0)
    return c, f"사용자가 직접 고른 참고 공지 · {_TIER_TEXT[c.tier]}"


# ── 프롬프트 ────────────────────────────────────────────────────────────
def _doc_tickers(c: Candidate | None) -> set[str]:
    if c is None:
        return set()
    return set(c.hit.tickers) | factcheck.paren_tickers(f"{c.hit.title}\n{c.hit.body}")


def _original(c: Candidate) -> tuple[str, str, list[str]]:
    """참고용 텍스트는 업데이트 전 최초 버전(제목 '(09/12 재개)' 꼬리표, 본문 위 재개 안내 제거).
    새 공지 초안은 최초 공지이므로 재개 안내까지 본뜰 필요가 없다."""
    return factcheck.original_version(c.hit.title, c.hit.body)


def _ref_text(c: Candidate, clip: int) -> str:
    """참고 공지의 제목+본문(최초 버전). 가리기와 자리 대응표가 같은 번호를 쓰도록 한 덩어리로 다룬다."""
    title, body, _ = _original(c)
    return f"{title}\n{body[:clip]}"


def _masked(c: Candidate, clip: int) -> tuple[str, str]:
    """제목과 본문을 함께 가린다. 따로 가리면 제목은 '<가상자산명>', 본문은 '<가상자산명1>'처럼
    번호 체계가 달라져 LLM이 코인 역할을 헷갈렸다."""
    title, _, body = factcheck.mask_reference(_ref_text(c, clip), _doc_tickers(c)).partition("\n")
    return title, body


def _slot_hints(parts: list[Part], c: Candidate) -> list[str]:
    """참고 공지의 가려진 코인 자리가 이번 공지의 어떤 입력값인지 코드가 알려준다.
    예) 에어드랍: <티커2> 자리(보유자) = 보유 기준 가상자산 → 네오(NEO)."""
    text = _ref_text(c, REF_CLIP)
    slots = factcheck.coin_slots(text, _doc_tickers(c))
    lines = []
    for p in parts:
        for rx, name in p.ntype.roles:
            v = p.inputs.get(name)
            if is_empty(v):
                continue
            for tk in dict.fromkeys(m.group("ticker") for m in re.finditer(rx, text)):
                if tk in slots:
                    lines.append(f"- 참고 공지의 {slots[tk]} 자리 = {field_label(p.ntype, name)} → "
                                 f"이번 공지에서는 {display_value(name, v)}")
    return list(dict.fromkeys(lines))


def _input_lines(parts: list[Part]) -> tuple[list[str], list[str]]:
    """(입력값 줄, 미입력 줄). 두 파트가 같은 값이면 한 번만 쓴다."""
    given: OrderedDict[tuple[str, str], list[str]] = OrderedDict()
    missing: OrderedDict[str, None] = OrderedDict()
    labels: dict[str, str] = {}
    for p in parts:
        for name in p.ntype.fields:
            labels.setdefault(name, field_label(p.ntype, name))
            v = p.inputs.get(name)
            if is_empty(v):
                if name not in p.ntype.required:
                    missing[name] = None
                continue
            given.setdefault((name, display_value(name, v)), []).append(p.category)
    lines = []
    for (name, val), cats in given.items():
        scope = f"({'/'.join(cats)} 파트) " if len(parts) > 1 and len(cats) < len(parts) else ""
        lines.append(f"- {scope}{labels[name]}: {val}")
    given_names = {n for n, _ in given}
    miss = [f"- {labels[n]}: 미입력" + (f" → {_MISSING_HINTS[n]}" if n in _MISSING_HINTS else "")
            for n in missing if n not in given_names]
    return lines, miss


def build_prompt(parts: list[Part], selected: Candidate | None, style: Candidate | None = None) -> tuple[str, str]:
    lines = ["[작성할 공지 유형]"]
    lines += [f"- {p.category} > {p.ntype.label}" for p in parts]
    if len(parts) > 1:
        lines.append("  → 두 유형을 한 공지로 합친다. 제목은 '… 및 …' 꼴, 본문에는 두 유형의 필수 항목을 모두 담는다.")

    given, missing = _input_lines(parts)
    lines.append("\n[이번 공지 입력값 — 유일한 사실 출처. 표기를 그대로 사용]")
    lines += given or ["- (없음)"]
    if missing:
        lines.append("\n[입력되지 않은 항목 — 값을 지어내지 말 것]")
        lines += missing

    hint = title_hint(parts)
    if hint:
        lines.append(f"\n[권장 제목] {hint}  (특별한 이유가 없으면 그대로 쓴다)")
    sections = list(dict.fromkeys(s for p in parts for s, _ in p.ntype.sections))
    if sections:
        lines.append("[반드시 포함할 항목] " + " / ".join(sections))

    if selected is not None:
        mt, mb = _masked(selected, REF_CLIP)
        lines += [
            "\n[참고 공지 — 형식·문체 전용]",
            "아래 공지의 사실값(가상자산명·티커·날짜·시각·차수·링크·조항 번호)은 <...> 또는 [..확인 필요]로 가려져 있다.",
            "- 가려진 자리는 위 입력값으로만 채우고, 입력값이 없으면 [확인 필요]로 둔다.",
            "- <가상자산명1>(<티커1>)처럼 번호가 붙었으면 같은 번호는 같은 가상자산이다. "
            "입력값의 역할(대상·지급·보유 기준 가상자산 등)에 맞게 대응시킨다.",
            "- 참고 공지에만 있는 상태·일정·수치 서술은 입력에 없으면 옮기지 않는다.",
            "- 참고 공지와 입력값이 다르면 항상 입력값을 따른다.",
            f"[제목] {mt}",
            f"[본문]\n{mb}",
        ]
        hints = _slot_hints(parts, selected)
        if hints:
            lines += ["\n[가려진 가상자산 자리 대응 — 코드가 확인한 역할이므로 그대로 따를 것]"] + hints
    else:
        lines.append("\n[참고 공지] 없음 — 같은 유형의 과거 공지를 찾지 못했다. 빗썸 공지의 일반 형식"
                     "(인사말 → 안내 문장 → 항목별 정리 → 유의사항 → 끝인사)으로 작성한다.")
    if style is not None:
        _, sb = _masked(style, STYLE_CLIP)
        lines += ["\n[최신 문체 참고 — 인사말·끝인사·호칭 표현만 참고]", sb]

    lines.append("\n위 입력값과 규칙에 따라 [출력 형식]대로 새 공지 초안을 작성하라.")
    return _system_prompt(parts), "\n".join(lines)


def build_revision_prompt(user_prompt: str, draft: str, problems: list[str]) -> str:
    return (
        f"{user_prompt}\n\n[1차 초안]\n{draft}\n\n[검토 결과 — 반드시 고칠 문제]\n"
        + "\n".join(f"- {p}" for p in problems)
        + "\n\n위 문제만 최소한으로 고쳐 같은 [출력 형식]으로 다시 작성하라. 지적되지 않은 부분은 그대로 둔다. "
          "입력에 없는 값은 지우거나 [확인 필요]로 바꾼다. "
          "문제에 문장이 인용돼 있으면 그 문장 자체를 고친다(다른 줄에 올바른 값이 있어도 인용된 문장을 반드시 고친다)."
    )


# ── 파이프라인 ──────────────────────────────────────────────────────────
@dataclass
class DraftOutcome:
    status: str = "ok"          # error | need_input | ready(prepare) | ok | needs_review
    parts: list[dict] = field(default_factory=list)
    estimated_subtypes: list[dict] = field(default_factory=list)   # 비슷한 공지로 추정한 유형과 근거(확인 필요)
    missing_fields: list[dict] = field(default_factory=list)
    invalid_fields: list[dict] = field(default_factory=list)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    query: str = ""
    retrieval_note: str = ""
    candidates: list[dict] = field(default_factory=list)
    selected_reference: dict | None = None
    selection_reason: str = ""
    title_hint: str = ""
    first_draft: str = ""
    first_check: dict | None = None
    first_evaluation: dict | None = None
    revision_attempted: bool = False
    revision_reasons: list[str] = field(default_factory=list)
    revised: bool = False
    final_draft: str = ""
    final_check: dict | None = None
    final_evaluation: dict | None = None
    needs_confirmation: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _answers_for_eval(parts: list[Part]) -> dict[str, str]:
    """평가기에 줄 요청 정보. 파트마다 값이 다르면 '라벨(카테고리 파트)'로 나눈다
    (라벨만 키로 쓰면 두 번째 파트 값이 첫 번째를 덮어써 멀쩡한 초안을 오판했다)."""
    given: OrderedDict[tuple[str, str], list[str]] = OrderedDict()
    labels: dict[str, str] = {}
    for p in parts:
        for n, v in p.inputs.items():
            if n in p.ntype.fields and not is_empty(v):
                labels.setdefault(n, field_label(p.ntype, n))
                given.setdefault((n, display_value(n, v)), []).append(p.category)
    out: dict[str, str] = {}
    for (n, val), cats in given.items():
        scoped = len(parts) > 1 and len(cats) < len(parts)
        out[f"{labels[n]}({'/'.join(cats)} 파트)" if scoped else labels[n]] = val
    return out


def _evaluate(parts, draft, chk, selected, eval_llm) -> Evaluation:
    answers = _answers_for_eval(parts)
    excerpt = _masked(selected, EVAL_REF_CLIP)[1] if selected else ""
    return evaluate(
        answers, draft, llm=eval_llm,
        notice_type=" + ".join(f"{p.category} > {p.ntype.label}" for p in parts),
        sections=[s for p in parts for s, _ in p.ntype.sections],
        code_issues=[f"[{i.severity}] {i.message}" for i in chk.issues],
        reference_excerpt=excerpt,
    )


def draft_notice(
    categories: list[str],
    *,
    text: str = "",
    inputs: dict | None = None,
    part_inputs: dict[str, dict] | None = None,
    subtypes: dict[str, str] | None = None,
    base_notice_url: str | None = None,
    evaluate_draft: bool = True,
    prepare_only: bool = False,
    llm: LLM | None = None,
    eval_llm: LLM | None = None,
    search: Callable = bm25_search,
    get_notice: Callable | None = None,
    now: datetime | None = None,
    top_k: int = 5,
    hybrid: bool = False,
    semantic: Callable | None = None,
) -> DraftOutcome:
    """전체 흐름. prepare_only=True면 후보·선택까지만(LLM 호출 없음).
    hybrid=True면 후보 검색·순위에 의미 검색(Bedrock 임베딩)을 섞는다. 실패하면 경고 후 BM25만."""
    out = DraftOutcome()
    res, out.estimated_subtypes = resolve_with_estimate(
        categories, text=text, inputs=inputs, part_inputs=part_inputs, subtypes=subtypes)
    out.parts = [p.to_dict() for p in res.parts]
    out.missing_fields, out.invalid_fields = res.missing, res.invalid
    out.errors, out.warnings = list(res.errors), list(res.warnings)
    if res.errors:
        out.status = "error"
        return out
    parts = res.parts
    out.title_hint = title_hint(parts)
    now = now or now_kst()      # 공지 시각은 KST. 서버가 UTC면 하루가 어긋난다

    # 후보는 필수값이 모자라도 보여준다(문답 중에 참고할 수 있게)
    out.query = build_query(parts, text)
    knn = similarity = None
    if hybrid:
        try:
            knn, similarity = (semantic or semantic_hooks)(out.query)   # 호출 시점에 찾는다(테스트에서 바꿔 끼움)
        except Exception as e:
            out.warnings.append(f"의미 검색을 쓰지 못해 BM25만 사용했습니다: {str(e)[:120]}")
    try:
        hits, out.retrieval_note = find_candidates(parts, out.query, search=search, semantic=knn)
    except Exception as e:
        hits, out.retrieval_note = [], "검색 실패"
        out.warnings.append(f"유사 공지 검색에 실패했습니다: {str(e)[:120]}")
    sims = None
    if similarity and hits:
        try:
            sims = similarity([h.source_url for h in hits])
        except Exception as e:
            out.warnings.append(f"의미 유사도를 구하지 못해 BM25 점수만 사용했습니다: {str(e)[:120]}")
    ranked = rank_candidates(hits, parts, now=now, sims=sims)
    out.candidates = [c.brief(i + 1) for i, c in enumerate(ranked[:top_k])]

    if base_notice_url:
        selected, out.selection_reason = _user_selected(base_notice_url, ranked, parts, now, get_notice)
        if selected is None:
            out.status = "error"
            out.errors.append(out.selection_reason)
            return out
        if selected.tier >= 3:
            out.warnings.append("직접 고른 참고 공지의 유형/카테고리가 요청과 다릅니다. 형식이 맞는지 확인하세요.")
    else:
        selected, out.selection_reason = select_reference(ranked)
    out.selected_reference = selected.brief() if selected else None
    if selected is None:
        out.warnings.append("참고할 과거 공지가 없어 일반 형식으로 작성합니다(형식 신뢰도 낮음).")
    else:
        orig_title, _, removed = _original(selected)
        out.selected_reference.update(original_title=orig_title, update_removed=removed)
        if removed:
            out.selection_reason += " · 재개 등 업데이트 부분은 빼고 최초 공지 기준으로 참고(" + ", ".join(removed) + ")"

    if not res.ok:
        out.status = "need_input"
        return out
    if prepare_only:
        out.status = "ready"
        return out

    style = _style_example(ranked, selected) if selected else None
    system, user = build_prompt(parts, selected, style)
    ref_tickers = _doc_tickers(selected) | _doc_tickers(style)
    ref_specific: set[str] = set()
    if selected is not None:
        # 같은 유형 다른 공지에도 있는 문장은 고정 문구, 선택 공지에만 있는 문장은 그 사례의 내용
        others = [(_original(c)[1], _doc_tickers(c)) for c in ranked
                  if c is not selected and c.tier == selected.tier][:BOILERPLATE_REFS]
        ref_specific = factcheck.specific_lines(_original(selected)[1][:REF_CLIP], _doc_tickers(selected), others)
    check = lambda d: factcheck.check_draft(d, parts, reference_tickers=ref_tickers, reference_specific=ref_specific)
    llm = llm or for_role("draft")

    draft = llm.generate(system, user, max_tokens=GEN_MAX_TOKENS)
    chk = check(draft)
    ev = _evaluate(parts, draft, chk, selected, eval_llm) if evaluate_draft else None
    out.first_draft, out.first_check = draft, chk.to_dict()
    out.first_evaluation = ev.to_dict() if ev else None

    if MAX_REVISIONS and (chk.errors or (ev and ev.needs_revision())):
        out.revision_attempted = True
        # 사실 오류 + 평가 지적 + (수정하는 김에) 빠진 필수 항목 경고까지 넘긴다
        out.revision_reasons = ([i.message for i in chk.errors] + (ev.problems() if ev else [])
                                + [i.message for i in chk.warnings if i.code == "section"])
        try:
            draft2 = llm.generate(system, build_revision_prompt(user, draft, out.revision_reasons),
                                  max_tokens=GEN_MAX_TOKENS)
        except Exception as e:   # 이미 만든(비용을 쓴) 1차 초안은 버리지 않는다
            draft2 = None
            out.warnings.append(f"수정 호출에 실패해 1차 초안을 최종본으로 유지했습니다: {str(e)[:120]}")
        if draft2 is not None:
            chk2 = check(draft2)
            if len(chk2.errors) > len(chk.errors):
                out.warnings.append("수정본의 사실 오류가 더 많아 1차 초안을 최종본으로 유지했습니다.")
            else:
                draft, chk, out.revised = draft2, chk2, True
                ev = _evaluate(parts, draft, chk, selected, eval_llm) if evaluate_draft else None

    out.final_draft, out.final_check = draft, chk.to_dict()
    out.final_evaluation = ev.to_dict() if ev else None
    out.needs_confirmation = chk.needs_confirmation
    out.warnings += [i.message for i in chk.warnings]
    out.status = "needs_review" if chk.errors else "ok"
    return out
