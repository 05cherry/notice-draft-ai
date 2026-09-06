"""쿼리 조립 — 문답(qa_context)을 검색 입력으로. 백엔드 무관.

1) 필터 vs 쿼리 분리: 정확일치(category, ticker)는 filters로, 의도 문장은 query_text로.
2) 문서를 닮은 조립: 답변을 실제 공지 제목 꼴 템플릿에 끼워 매칭률을 올림.
"""

from __future__ import annotations

import re
import string
from dataclasses import dataclass, field

from notice_ai import aliases

_TEMPLATES: dict[str, str] = {
    "deposit-withdrawal": "{coin_kr}({ticker}) {action} 안내",
    "trading-caution": "{coin_kr}({ticker}) 거래유의종목 지정",
    "delisting": "{coin_kr}({ticker}) 거래지원 종료",
    "market-listing": "{coin_kr}({ticker}) 원화 마켓 추가",
    "maintenance": "{action} 점검 안내",
    "general": "{action} 안내",
}
_DEFAULT = "{coin_kr}({ticker}) {action}"


@dataclass(frozen=True)
class AssembledQuery:
    query_text: str
    filters: dict = field(default_factory=dict)   # {"category": ..., "ticker": ...}
    expansion_terms: list[str] = field(default_factory=list)


def assemble(
    qa_context: dict,
    category_slug: str,
    category_name: str | None = None,
) -> AssembledQuery:
    ctx = {k: (v or "") for k, v in qa_context.items()}
    template = _TEMPLATES.get(category_slug, _DEFAULT)
    try:
        base = template.format(**{**_empty_keys(template), **ctx})
    except KeyError:
        base = _DEFAULT.format(
            coin_kr=ctx.get("coin_kr", ""),
            ticker=ctx.get("ticker", ""),
            action=ctx.get("action", ""),
        )
    base = _tidy(base)
    reason = ctx.get("reason", "").strip()
    query_text = _tidy(f"{base} {reason}")

    filters: dict = {}
    if category_name:
        filters["category"] = category_name
    if ctx.get("ticker"):
        filters["ticker"] = ctx["ticker"].upper()

    exp = aliases.expand(ctx.get("ticker") or None, ctx.get("coin_kr") or None)
    return AssembledQuery(query_text=query_text, filters=filters, expansion_terms=exp)


def _empty_keys(template: str) -> dict:
    keys = [fn for _, fn, _, _ in string.Formatter().parse(template) if fn]
    return {k: "" for k in keys}


def _tidy(s: str) -> str:
    s = s.replace("()", " ")
    s = re.sub(r"\s+", " ", s)
    return s.strip()
