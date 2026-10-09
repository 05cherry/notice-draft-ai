"""카테고리마다 제목이 유형으로 얼마나 잡히는지 센다 (#10).

유형을 늘리는 일에는 재는 수단이 있어야 한다. 유형이 없으면 공지는 `<카테고리>/general`로
가는데, general 은 필수가 '공지 주제'와 '핵심 내용'뿐이라 제목 틀·섹션 검사·라벨 대조가
전부 없다. 그래서 **general 로 가는 비율**이 그 카테고리에 유형이 얼마나 필요한지를 그대로
나타낸다. 유형을 넣은 뒤 이 비율이 떨어져야 일을 한 것이다.

`subtype_check` 와 겹치지 않는다. 그쪽은 형제 유형(family)이 본문으로 갈리는지를 재고(#38),
여기서는 제목이 유형에 걸리는지만 센다. 본문을 받지 않으므로 훑는 비용도 훨씬 작다.

general 로 간 제목을 같이 돌려준다 — 거기에 다음에 만들 유형이 들어 있다. 반복되는 표현이
보이면 그게 유형이고, 제각각이면 그 카테고리는 general 로 두는 게 맞다.

색인을 건드리지 않는다. 읽기만 한다.
"""

from __future__ import annotations

import logging

from notice_ai import index_ref
from notice_ai.notice_types import TARGET_CATEGORIES, route
from notice_ai.opensearch_client import get_client

logger = logging.getLogger(__name__)

SCAN_PAGE = 500      # 한 번에 받아 올 문서 수
EXAMPLES = 15        # 카테고리마다 보여 줄 general 제목 수


def _titles(client, index: str, categories: list[str]):
    """(제목, 카테고리들). 본문·임베딩은 받지 않는다 — 제목만 보는 진단이다."""
    from opensearchpy import helpers

    query = {"terms": {"categories": categories}} if categories else {"match_all": {}}
    for h in helpers.scan(client, index=index, size=SCAN_PAGE, preserve_order=False,
                          query={"_source": ["title", "categories"], "query": query}):
        s = h.get("_source") or {}
        yield s.get("title") or "", s.get("categories") or []


def diagnose(category: str = "") -> dict:
    """카테고리별 subtype 분포와 general 비율.

    category 를 비우면 유형이 정의된 카테고리(TARGET_CATEGORIES)를 전부 본다.
    돌려주는 모양은 항상 같다 — 실패해도 error 만 차고 나머지는 빈 값이다.
    """
    out: dict = {"error": "", "index": "", "checked": 0, "categories": []}

    cats = [category] if category else list(TARGET_CATEGORIES)
    unknown = [c for c in cats if c not in TARGET_CATEGORIES]
    if unknown:
        out["error"] = (f"'{unknown[0]}'에는 유형이 정의되어 있지 않습니다. "
                        f"정의된 카테고리: {', '.join(TARGET_CATEGORIES)}")
        return out

    try:
        client = get_client()
        out["index"] = index_ref.target(client)
    except Exception as e:
        out["error"] = f"검색 서버에 붙지 못했습니다 — {type(e).__name__}"
        return out

    # 카테고리마다 {subtype: 건수}. 한 공지가 카테고리 둘에 걸리면 양쪽에서 센다.
    counts: dict[str, dict[str, int]] = {c: {} for c in cats}
    samples: dict[str, list[str]] = {c: [] for c in cats}
    try:
        for title, doc_cats in _titles(client, out["index"], cats):
            if not title:
                continue
            out["checked"] += 1
            for c in cats:
                if doc_cats and c not in doc_cats:
                    continue
                sub = route(c, title)[0].subtype
                counts[c][sub] = counts[c].get(sub, 0) + 1
                if sub == "general" and len(samples[c]) < EXAMPLES:
                    samples[c].append(title)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {' '.join(str(e).split())[:200]}"
        return out

    for c in cats:
        total = sum(counts[c].values())
        general = counts[c].get("general", 0)
        out["categories"].append({
            "category": c,
            "total": total,
            "general": general,
            # 이 카테고리 공지 중 유형이 안 잡힌 비율. 낮을수록 좋다.
            "general_ratio": round(general / total, 3) if total else 0.0,
            "subtypes": dict(sorted(((k, v) for k, v in counts[c].items() if k != "general"),
                                    key=lambda kv: -kv[1])),
            "general_titles": samples[c],
        })
    return out
