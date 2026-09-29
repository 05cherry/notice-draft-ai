"""짧은 줄을 복사 판정에 넣으면 어떻게 되는지 센다 (#36).

초안이 참고 공지의 '그 사례에만 있던 내용'을 베끼면 잡는 검사가 있다(`factcheck.specific_lines`
→ `check_draft`의 reference_copy). 그런데 `MIN_COPY_LINE = 30`보다 짧은 줄은 양쪽에서 빠진다 —
참고 공지에서 '고유 줄'을 모을 때도, 초안 줄을 검사할 때도.

그래서 표 형태 공지처럼 값이 따로 한 줄에 오면 새어 나간다. 실측 예:
    [26자] 제3조 (약관의 명시, 설명과 개정)   ← 문턱 아래. 그대로 베껴도 통과
    [42자] 개정 조항 : 제3조 (…)에 관한 사항  ← 같은 내용인데 길어서 잡힘

문턱을 낮추는 게 답인지는 재봐야 안다. 짧은 줄은 대부분 항목명('개정 조항')·인사말이라,
문턱만 낮추면 정상 공지가 전부 복사로 잡힐 수 있다.

그런데 `specific_lines`는 이미 길이보다 나은 판정을 갖고 있다 — **같은 유형 다른 공지에도
있는 줄인가**. 비교용 풀(`common`)은 이미 15자부터 모으고 있어서, 짧은 줄은 그 판정에
실패한 게 아니라 판정 자체에서 빠져 있을 뿐이다. 그래서 여기서 재는 것은 둘이다.

  고정 문구   짧은 줄인데 같은 유형 다른 공지에도 있다 → 문턱을 낮춰도 안 잡힌다(안전)
  사례 고유   짧은 줄인데 이 공지에만 있다 → 지금 검사가 놓치고 있는 것

'사례 고유'가 적으면 #36은 실제 문제가 아니다. 많으면 예시를 보고 문턱을 낮출지 정한다.

색인을 건드리지 않는다. 읽기만 한다.
"""

from __future__ import annotations

import logging

from notice_ai import index_ref
from notice_ai.factcheck import COMMON_SIM, MIN_COPY_LINE, _norm_line, _similar_any, original_version
from notice_ai.notice_types import TARGET_CATEGORIES, classify_title
from notice_ai.opensearch_client import get_client
from notice_ai.subtype_check import BODY_HEAD, SCAN_PAGE

logger = logging.getLogger(__name__)

COMMON_FLOOR = 15    # specific_lines 의 비교 풀이 모으는 최소 길이. 문턱 후보의 하한
SIBLINGS = 8         # 고정 문구 판정에 쓸 같은 유형 공지 수(drafting.BOILERPLATE_REFS 와 같게)
MIN_BODY = 100       # drafting 과 같게. 본문이 짧은 공지는 참고로 안 쓰므로 여기서도 뺀다
SNIPPET = 60


def _groups(client, index: str, cap: int) -> dict[tuple[str, str], list[tuple[str, str]]]:
    """(카테고리, subtype) → [(제목, 본문)]. drafting 과 같은 방식으로 유형을 매긴다."""
    from opensearchpy import helpers

    out: dict[tuple[str, str], list[tuple[str, str]]] = {}
    for h in helpers.scan(client, index=index, size=SCAN_PAGE, preserve_order=False,
                          query={"_source": ["title", "raw_text", "categories"],
                                 "query": {"terms": {"categories": list(TARGET_CATEGORIES)}}}):
        s = h.get("_source") or {}
        title, raw = s.get("title") or "", s.get("raw_text") or ""
        cats = [c for c in (s.get("categories") or []) if c in TARGET_CATEGORIES]
        if not title or not cats:
            continue
        body = original_version(title, raw)[1][:BODY_HEAD]
        if len(body.strip()) < MIN_BODY:      # 참고 공지로 쓰이지 않는 것은 셀 이유가 없다
            continue
        subs = classify_title(title, cats, body=body)
        for c in cats:
            key = (c, subs.get(c, "general"))
            bucket = out.setdefault(key, [])
            if len(bucket) < cap:
                bucket.append((title, body))
    return out


def diagnose(low: int = COMMON_FLOOR, per_group: int = 40) -> dict:
    """짧은 줄(low ~ MIN_COPY_LINE)을 고정 문구와 사례 고유로 갈라 센다.

    low 는 새 문턱 후보. 기본값은 비교 풀이 이미 모으는 15자.
    per_group 은 (카테고리, subtype)마다 볼 공지 수 — 전수는 difflib 비교가 너무 많아진다.
    """
    # 비교 풀도 같이 내려야 한다. specific_lines 의 풀은 15자부터 모으므로, low 만 내리면
    # 그 짧은 줄들이 풀에 없어 전부 '고유'로 보인다(항목명 '개정 조항'까지 복사로 잡힌다).
    # 문턱을 내리는 것은 풀 하한을 내리는 것과 한 짝이고, 진단은 그 짝을 함께 재야 한다.
    floor = min(low, COMMON_FLOOR)
    out: dict = {"error": "", "index": "", "low": low, "high": MIN_COPY_LINE, "pool_floor": floor,
                 "groups": 0, "notices": 0, "short_lines": 0,
                 "boilerplate": 0, "case_specific": 0, "long_specific": 0,
                 "by_group": [], "examples": []}
    if low >= MIN_COPY_LINE:
        out["error"] = f"low({low})가 지금 문턱({MIN_COPY_LINE}) 이상이라 볼 구간이 없습니다."
        return out
    try:
        client = get_client()
        out["index"] = index_ref.target(client)
        groups = _groups(client, out["index"], per_group)
    except Exception as e:
        out["error"] = f"{type(e).__name__}: {' '.join(str(e).split())[:200]}"
        return out

    examples: list[dict] = []
    for (cat, sub), docs in sorted(groups.items()):
        if len(docs) < 2:
            continue        # 비교할 공지가 없으면 고정 문구인지 판단할 수 없다(specific_lines 와 같다)
        out["groups"] += 1
        g = {"category": cat, "subtype": sub, "notices": len(docs),
             "short_lines": 0, "boilerplate": 0, "case_specific": 0, "long_specific": 0}
        for i, (title, body) in enumerate(docs):
            others = [d for j, d in enumerate(docs) if j != i][:SIBLINGS]
            # specific_lines 와 같은 풀(하한만 floor 로 맞춘다)
            common = {n for _, b in others for ln in b.splitlines()
                      if len(n := _norm_line(ln, set())) >= floor}
            out["notices"] += 1
            for ln in body.splitlines():
                n = _norm_line(ln, set())
                if not n:
                    continue
                known = n in common or _similar_any(n, common, COMMON_SIM)
                if len(n) >= MIN_COPY_LINE:
                    if not known:
                        g["long_specific"] += 1      # 지금도 잡히는 것. 비교용 기준선
                    continue
                if len(n) < low:
                    continue
                g["short_lines"] += 1
                if known:
                    g["boilerplate"] += 1
                else:
                    g["case_specific"] += 1
                    if len(examples) < 40:
                        examples.append({"category": cat, "subtype": sub,
                                         "title": title[:SNIPPET], "line": n, "chars": len(n)})
        for k in ("short_lines", "boilerplate", "case_specific", "long_specific"):
            out[k] += g[k]
        out["by_group"].append(g)

    out["by_group"].sort(key=lambda x: -x["case_specific"])
    out["examples"] = examples
    return out
