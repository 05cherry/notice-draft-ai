"""형제 유형(family) 판별이 실제 색인에서 얼마나 맞는지 센다 (#38).

에어드랍처럼 제목만으로 '지급 완료'와 '지급 예정'이 안 갈리는 유형은 과거 공지 **본문 표현**으로
가른다(`notice_types.route`의 family/body_pattern). 빗썸이 새 표현을 쓰면 규칙이 못 잡는데,
그때 어떻게 되는지·몇 건인지 아무도 모르는 채로 두었다.

여기서 재는 것은 셋이다.
  맞음        본문이 제목으로 고른 유형의 규칙에 맞다
  형제로 감   본문이 형제 쪽 규칙에 맞아 그쪽으로 간다(규칙이 의도대로 동작)
  아무 말 없음  본문이 어느 형제 규칙에도 안 맞는다 — 판단 근거가 제목뿐이다  ← 재려는 것

`route`의 결과로 세지 않고 규칙을 직접 본다. route는 본문이 아무 말도 안 하면 제목의
유형을 그대로 쓰므로(규칙은 뒤집을 때만 쓴다), 결과만 보면 '아무 말 없음'이 안 보인다.
재려는 것은 라우팅 결과가 아니라 **본문 규칙이 얼마나 일을 하는가**다.

마지막 것은 원인이 둘이라 갈라서 센다. 고치는 방법이 서로 다르기 때문이다.
  본문이 비어 있음  수집을 --no-body로 했거나 본문이 이미지뿐(#37). 규칙을 늘려도 안 고쳐진다
  본문은 있는데 안 맞음  새 표현. 본문을 모아 규칙에 넣으면 고쳐진다

색인을 건드리지 않는다. 읽기만 한다.
"""

from __future__ import annotations

import logging

import re

from notice_ai import factcheck, index_ref
from notice_ai.notice_types import TARGET_CATEGORIES, get_type, route, types_for
from notice_ai.opensearch_client import get_client

logger = logging.getLogger(__name__)

SCAN_PAGE = 500      # 한 번에 받아 올 문서 수
BODY_HEAD = 1500     # 유형 판별에 쓰는 본문 앞부분. drafting 과 같은 값이어야 한다
SNIPPET = 160        # 예시에 보여 줄 본문 길이


def families(category: str) -> dict[str, list[str]]:
    """그 카테고리에서 본문으로 갈리는 유형들 {family: [subtype...]}."""
    out: dict[str, list[str]] = {}
    for t in types_for(category):
        if t.family and t.body_pattern:
            out.setdefault(t.family, []).append(t.subtype)
    return {k: sorted(v) for k, v in out.items() if len(v) > 1}


def _scan(client, index: str, categories: list[str], limit: int, offset: int):
    """제목·본문·카테고리만 받아 온다. 임베딩은 크니 빼고.

    from/size 로 넘기면 max_result_window(기본 1만)에서 막힌다. 지금 공지는 그보다 적지만
    계속 쌓이는 것이라 scroll(helpers.scan)로 훑는다. offset 은 받아 놓고 건너뛴다.
    """
    from opensearchpy import helpers

    query = {"terms": {"categories": categories}} if categories else {"match_all": {}}
    seen = 0
    for i, h in enumerate(helpers.scan(
            client, index=index, size=SCAN_PAGE, preserve_order=False,
            query={"_source": ["title", "raw_text", "categories"], "query": query})):
        if i < offset:
            continue
        if limit and seen >= limit:
            return
        s = h.get("_source") or {}
        yield s.get("title") or "", s.get("raw_text") or "", s.get("categories") or []
        seen += 1


def diagnose(category: str = "", limit: int = 0, offset: int = 0) -> dict:
    """색인된 공지를 제목만으로 / 본문까지 보고 각각 분류해 어긋나는 것을 센다.

    category를 비우면 본문 규칙이 있는 카테고리를 전부 본다.
    돌려주는 모양은 항상 같다 — 실패해도 error 만 차고 나머지는 빈 값이다.
    """
    out: dict = {"error": "", "index": "", "categories": [], "families": {},
                 "checked": 0, "in_family": 0, "agree": 0, "sibling": 0, "no_body_signal": 0,
                 "empty_body": 0, "novel_wording": 0,
                 "empty_examples": [], "novel_examples": [], "sibling_examples": []}

    cats = [category] if category else [c for c in TARGET_CATEGORIES if families(c)]
    cats = [c for c in cats if families(c)]
    if not cats:
        out["error"] = (f"'{category}'에는 본문으로 갈리는 유형이 없습니다."
                        if category else "본문으로 갈리는 유형이 있는 카테고리가 없습니다.")
        return out
    out["categories"] = cats
    out["families"] = {c: families(c) for c in cats}

    try:
        client = get_client()
        out["index"] = index_ref.target(client)
    except Exception as e:
        out["error"] = f"검색 서버에 붙지 못했습니다 — {type(e).__name__}"
        return out

    empty, novel, sib = [], [], []
    try:
        for title, raw, doc_cats in _scan(client, out["index"], cats, limit, offset):
            if not title:
                continue
            out["checked"] += 1
            # drafting 과 같은 방식으로 본문을 다듬는다(업데이트 꼬리표·덧붙은 안내 제거)
            body = factcheck.original_version(title, raw)[1][:BODY_HEAD]
            for c in cats:
                if doc_cats and c not in doc_cats:
                    continue
                by_title = route(c, title)[0].subtype           # 본문을 안 보면
                if by_title == "general":
                    continue                                    # 제목부터 안 잡히면 이 이슈와 무관
                t = get_type(c, by_title)
                if not (t and t.family):
                    continue        # 본문으로 갈릴 일이 없는 유형. 세면 늘 '맞음'이라 숫자만 부푼다
                out["in_family"] += 1
                if t.body_pattern and re.search(t.body_pattern, body):
                    out["agree"] += 1
                    continue
                kin = next((x for x in types_for(c) if x is not t and x.family == t.family
                            and x.body_pattern and re.search(x.body_pattern, body)), None)
                if kin:
                    out["sibling"] += 1
                    if len(sib) < 10:
                        sib.append({"title": title, "by_title": by_title, "final": kin.subtype,
                                    "body": body[:SNIPPET]})
                    continue
                # 본문이 아무 말도 안 한다. route는 제목의 유형을 쓰므로 공지가 사라지진 않지만,
                # 이 수가 크면 본문 규칙이 일을 안 하고 제목에만 기대고 있다는 뜻이다.
                out["no_body_signal"] += 1
                if not body.strip():
                    out["empty_body"] += 1
                    if len(empty) < 10:
                        empty.append({"title": title, "by_title": by_title, "body": ""})
                else:
                    out["novel_wording"] += 1
                    if len(novel) < 20:
                        novel.append({"title": title, "by_title": by_title,
                                      "body": body[:SNIPPET]})
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {' '.join(str(e).split())[:200]}"
        return out

    out.update(empty_examples=empty, novel_examples=novel, sibling_examples=sib)
    return out
