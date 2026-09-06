"""공지 초안 생성 (기능 2).

흐름:
  1) 기준 공지(사용자가 고른 것) 원문을 OpenSearch에서 가져온다.
  2) 같은 유형의 '최신' 유사 공지 몇 건을 검색해 예시로 곁들인다(어투·인사말 최신 기준).
  3) 지침 시스템 프롬프트 + 기준/유사 공지 + 문답값을 LLM에 넘겨 초안 생성.
  4) 고위험 값(티커·날짜·수치)이 초안에 그대로 반영됐는지 코드로 검증 → 경고 반환.

원칙: 고위험 값은 LLM이 지어내지 않는다. 문답값을 그대로 주입하고, 사후 검증한다.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

from notice_ai import config
from notice_ai.llm import get_llm
from notice_ai.opensearch_client import get_client
from notice_ai.search import bm25_search

_PROMPT_DIR = Path(__file__).resolve().parent / "prompts"
_COMMON = (_PROMPT_DIR / "common.txt").read_text(encoding="utf-8")


def _system_prompt(category: str | None) -> str:
    """공통 지침 + (있으면) 카테고리별 지침을 합쳐 system 프롬프트로."""
    if not category:
        return _COMMON
    cat_file = _PROMPT_DIR / "category" / f"{category}.txt"
    if cat_file.exists():
        return _COMMON + "\n\n" + cat_file.read_text(encoding="utf-8")
    return _COMMON

_LATEST_SIMILAR_N = 3   # 곁들일 최신 유사 공지 수
_BODY_CLIP = 1800       # 예시로 넣을 본문 길이 제한(토큰 절약)


@dataclass
class DraftResult:
    draft: str
    warnings: list[str] = field(default_factory=list)
    base_url: str | None = None
    referenced: list[str] = field(default_factory=list)


def _get_notice(source_url: str) -> dict | None:
    client = get_client()
    try:
        return client.get(index=config.INDEX_NAME, id=source_url)["_source"]
    except Exception:
        return None


def _latest_similar(query: str, category: str | None, exclude_url: str) -> list[dict]:
    """같은 유형 최신 유사 공지. 검색으로 뽑아 최신순 정렬 후 상위 N."""
    hits = bm25_search(query, {"category": category} if category else {}, size=15)
    docs = []
    for h in hits:
        if h.source_url == exclude_url:
            continue
        src = _get_notice(h.source_url)
        if src:
            docs.append(src)
    # 최신순 정렬(published_at 문자열이 ISO라 문자열 비교로 충분)
    docs.sort(key=lambda d: d.get("published_at") or "", reverse=True)
    return docs[:_LATEST_SIMILAR_N]


def _fmt_notice(d: dict, clip: int = _BODY_CLIP) -> str:
    body = (d.get("raw_text") or "")[:clip]
    return (
        f"[제목] {d.get('title','')}\n"
        f"[카테고리] {'/'.join(d.get('categories') or [])}\n"
        f"[게시일] {d.get('published_at','')}\n"
        f"[본문]\n{body}"
    )


def _fixed_values_block(answers: dict) -> str:
    """LLM이 그대로 써야 하는 고위험 값들."""
    lines = [f"- {k}: {v}" for k, v in answers.items() if v]
    return "\n".join(lines) if lines else "(없음)"


def _high_risk_values(answers: dict) -> list[str]:
    """초안에 반드시 들어가야 하는 값(검증 대상)."""
    keys = ("ticker", "coin_kr", "coin_en", "datetime", "date")
    vals = []
    for k in keys:
        v = str(answers.get(k, "")).strip()
        if v:
            vals.append(v)
    return vals


# 날짜 안의 숫자만 뽑아 비교(형식 무시). '2026-09-01'과 '2026.09.01(금)'을 같게 본다.
_DIGITS_RE = re.compile(r"\d+")
# 법령 조항 패턴: 조/호/법령명만 잡는다. '목'은 제외(제목·항목 등 오탐 방지).
# 조·호가 이미 강한 신호라, 조항 인용이면 거의 항상 '제N조' 또는 '제N호'가 있다.
_LAW_RE = re.compile(r"제\s*\d+\s*조|제\s*\d+\s*호|가상자산이용자보호법|시행령")


def _value_present(val: str, draft: str) -> bool:
    """고정 값이 초안에 들어갔는지. 날짜류는 숫자 시퀀스로 관대하게 비교."""
    if val in draft:
        return True
    nums = _DIGITS_RE.findall(val)
    # 숫자가 2개 이상(연·월·일 등)인 값은 날짜로 보고, 그 숫자들이 초안에 순서대로 있으면 통과
    if len(nums) >= 2:
        draft_nums = _DIGITS_RE.findall(draft)
        # 값의 숫자 시퀀스가 초안 숫자들의 부분 시퀀스인지 확인
        i = 0
        for dn in draft_nums:
            if i < len(nums) and dn == nums[i]:
                i += 1
        if i == len(nums):
            return True
    return False


def generate_draft(base_url: str, answers: dict, category: str | None = None) -> DraftResult:
    base = _get_notice(base_url)
    if base is None:
        return DraftResult(draft="", warnings=[f"기준 공지를 찾을 수 없습니다: {base_url}"])

    category = category or (base.get("categories") or [None])[0]
    query = f"{answers.get('coin_kr','')} {answers.get('action','')} {answers.get('reason','')}".strip()
    similars = _latest_similar(query or base.get("title", ""), category, base_url)

    # 프롬프트 조립
    parts = [
        "다음은 형식을 본떠야 할 '기준 공지'다.",
        _fmt_notice(base),
        "\n다음은 참고할 '최신 유사 공지'들이다(최신순). 인사말·어투·유의사항은 이쪽 최신 표현을 우선한다.",
    ]
    for i, s in enumerate(similars, 1):
        parts.append(f"\n--- 최신 유사 공지 {i} ---\n{_fmt_notice(s)}")
    parts.append(
        "\n[이번 공지의 고정 값 — 반드시 그대로 사용, 임의 변경 금지]\n"
        + _fixed_values_block(answers)
    )
    parts.append(
        "\n위 기준 공지의 형식과 최신 유사 공지의 어투를 따라, 고정 값을 반영한 새 공지 초안을 "
        "정해진 출력 형식으로 작성하라. 제공되지 않은 사실은 [확인 필요]로 표시하라."
    )
    user_prompt = "\n".join(parts)

    draft = get_llm().generate(_system_prompt(category), user_prompt)

    # 고위험 값 사후 검증
    warnings: list[str] = []
    for val in _high_risk_values(answers):
        if not _value_present(val, draft):
            warnings.append(f"고정 값 '{val}'이(가) 초안에 보이지 않습니다. 확인 필요.")

    # 법령 조항이 들어갔으면 항상 검증 요청(사유마다 조항이 다르므로 사람이 확인)
    if _LAW_RE.search(draft):
        warnings.append(
            "초안에 법령/규정 조항이 포함되어 있습니다. 사유에 맞는 조항인지 반드시 검증 필요."
        )

    return DraftResult(
        draft=draft,
        warnings=warnings,
        base_url=base_url,
        referenced=[s.get("source_url", "") for s in similars],
    )
