"""맞춤법 검사 (기능 3, #5).

  초안 + (있으면) 입력값 → LLM 1회 → 고칠 곳 제안 목록

대원칙이 여기서도 걸린다. 맞춤법 검사가 본문을 고쳐 돌려주면 사실값이 조용히 바뀐다.

    헤데라(HBAR)   → 헤더라(HBAR)      코인명이 바뀐다
    2026.09.25(금) → 2026.09.25(목)    요일이 바뀐다
    빗썸           → 빗금              고유명사

factcheck 는 초안과 입력값을 대조하는데, 교정한 뒤에 다시 대조하지 않으면 이걸 못 잡는다.
그래서 여기서는 **고쳐진 본문을 만들지 않는다.** 위치가 딸린 제안만 돌려주고, 사실값과 겹치는
제안은 코드가 걸러낸다(protected). 무엇을 막았는지도 함께 돌려주므로 검사기가 헛짚는지 보인다.

LLM 이 지어낸 제안(본문에 없는 before)도 코드가 버린다 — /extract 와 같은 방식이다.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path

from notice_ai import aliases, factcheck
from notice_ai.extract import parse_json
from notice_ai.llm import LLM, for_role
from notice_ai.notice_types import (
    FIELDS,
    Part,
    coin_label,
    display_value,
    is_empty,
    is_temporal,
    is_unknown,
)

_PROMPT = (Path(__file__).resolve().parent / "prompts" / "spellcheck.txt").read_text(encoding="utf-8")

MAX_TOKENS = 1200
MAX_DRAFT = 6000        # 공지 한 편은 이보다 짧다. 넘으면 자른다
KINDS = frozenset({"spelling", "spacing", "wording", "punctuation"})
MIN_LEN = 2             # 한 글자짜리 제안은 어디를 가리키는지 알 수 없어 버린다

# '헤데라(HBAR)' 에서 이름 쪽. 바로 앞 낱말만 잡는다(공백을 넣으면 앞 구절까지 딸려 온다).
_NAME_BEFORE_TICKER = re.compile(r"([가-힣A-Za-z0-9.\-]+)\s*\(\s*[A-Z0-9]{2,12}\s*\)")


@dataclass
class SpellOutcome:
    status: str = "ok"                                    # error | ok
    suggestions: list[dict] = field(default_factory=list)  # {before, after, reason, kind, positions}
    # 사실값과 겹쳐서 막은 제안. 검사기가 엉뚱한 곳을 건드리려 했는지 보여 준다
    protected: list[dict] = field(default_factory=list)    # {before, after, guarded}
    dropped: int = 0                                       # 본문에 없는 before(지어낸 제안) 수
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _add_spans(draft: str, needle: str, why: str, out: list[tuple[int, int, str]]) -> None:
    if not needle or len(needle) < MIN_LEN:
        return
    start = 0
    while (i := draft.find(needle, start)) >= 0:
        out.append((i, i + len(needle), why))
        start = i + 1


def protected_spans(draft: str, parts: list[Part] | None = None) -> list[tuple[int, int, str]]:
    """건드리면 안 되는 자리. (시작, 끝, 무엇인지)

    두 갈래로 모은다.
      1) 사용자가 넣은 입력값 — 코인·일시·링크·고유명(exact) 등. 이게 곧 사실값이다.
      2) 초안 안에서 형태로 알아볼 수 있는 것 — 날짜·시각·URL·(티커)·조항 번호.
         입력에 없어도 맞춤법 검사기가 건드리면 안 되는 것들이다.
    """
    spans: list[tuple[int, int, str]] = []

    for p in parts or []:
        for name, v in p.inputs.items():
            if is_empty(v) or is_unknown(v):
                continue
            f = FIELDS.get(name)
            if f and f.kind == "coins":
                for c in v:
                    _add_spans(draft, c.get("name", ""), "가상자산 이름", spans)
                    _add_spans(draft, c.get("ticker", ""), "티커", spans)
                _add_spans(draft, coin_label(v), "가상자산", spans)
            elif f and is_temporal(f.kind):
                _add_spans(draft, display_value(name, v), "일시", spans)
                _add_spans(draft, str(v), "일시", spans)
            elif f and f.kind == "urls":
                for u in v:
                    _add_spans(draft, str(u), "링크", spans)
            elif f and f.exact:
                _add_spans(draft, str(v), f"{f.label}(입력 그대로 써야 하는 값)", spans)

    for m in factcheck._URL_RE.finditer(draft):
        spans.append((m.start(), m.end(), "링크"))
    # 코인 이름은 입력값이 없어도 지킨다. 사전에 없는 말이라 검사기가 제일 먼저 건드린다.
    for m in _NAME_BEFORE_TICKER.finditer(draft):
        spans.append((m.start(1), m.end(1), "가상자산 이름"))
    for tk in factcheck.paren_tickers(draft):
        _add_spans(draft, f"({tk})", "티커", spans)
        _add_spans(draft, tk, "티커", spans)
        # 티커를 알면 그 코인의 다른 이름도 지킨다(본문에 티커 없이 나오는 자리)
        for alias in aliases.expand(tk):
            _add_spans(draft, alias, "가상자산 이름", spans)
    for *_, raw in factcheck.extract_dates(draft):
        _add_spans(draft, raw, "날짜", spans)
    for *_, raw in factcheck.extract_times(draft):
        _add_spans(draft, raw, "시각", spans)
    for c in factcheck.clauses(draft):
        _add_spans(draft, c, "조항", spans)
    return spans


def _guard(before: str, positions: list[int], spans: list[tuple[int, int, str]]) -> str | None:
    """이 제안이 사실값을 건드리는가. 건드리면 무엇인지, 아니면 None."""
    for pos in positions:
        lo, hi = pos, pos + len(before)
        for s, e, why in spans:
            if lo < e and s < hi:      # 겹친다
                return why
    return None


def build_prompt(title: str, body: str) -> tuple[str, str]:
    return _PROMPT, f"제목: {title}\n본문:\n{body}"[:MAX_DRAFT]


def check_spelling(
    draft: str = "",
    *,
    title: str = "",
    body: str = "",
    parts: list[Part] | None = None,
    llm: LLM | None = None,
) -> SpellOutcome:
    """초안의 고칠 곳을 찾는다. LLM 1회. 본문을 고쳐 주지는 않는다.

    draft('제목: …/본문: …') 또는 title·body 중 하나를 준다.
    parts 를 주면 그 입력값(코인·일시·링크·고유명)까지 지킨다. 없으면 초안에서 알아볼 수 있는
    것(날짜·시각·URL·티커·조항)만 지킨다.
    """
    out = SpellOutcome()
    if draft and not (title or body):
        title, body, _, _ = factcheck.parse_draft(draft)
    text = f"제목: {title}\n본문:\n{body}"
    if not (title.strip() or body.strip()):
        out.warnings.append("검사할 초안이 비어 있습니다.")
        return out

    system, user = build_prompt(title, body)
    answer = (llm or for_role("spell")).generate(system, user, max_tokens=MAX_TOKENS)

    data = parse_json(answer)
    if data is None or not isinstance(data.get("suggestions"), list):
        out.warnings.append("검사 결과를 읽지 못했습니다(JSON이 아님). 다시 시도해 보세요.")
        return out

    spans = protected_spans(text, parts)
    seen: set[tuple[str, str]] = set()
    for item in data["suggestions"]:
        if not isinstance(item, dict):
            continue
        before, after = str(item.get("before") or ""), str(item.get("after") or "")
        if len(before) < MIN_LEN or before == after:
            continue
        if (before, after) in seen:
            continue
        seen.add((before, after))

        positions = []
        start = 0
        while (i := text.find(before, start)) >= 0:
            positions.append(i)
            start = i + 1
        if not positions:
            out.dropped += 1       # 본문에 없는 말을 고치라고 한다 = 지어낸 제안
            continue

        guarded = _guard(before, positions, spans)
        row = {"before": before, "after": after,
               "reason": str(item.get("reason") or ""),
               "kind": k if (k := str(item.get("kind") or "")) in KINDS else "wording"}
        if guarded:
            out.protected.append({**row, "guarded": guarded})
        else:
            out.suggestions.append({**row, "positions": positions})

    if out.dropped:
        out.warnings.append(f"본문에 없는 곳을 고치라는 제안 {out.dropped}건은 버렸습니다.")
    if out.protected:
        out.warnings.append(
            f"가상자산·일시·링크 같은 사실값을 고치려는 제안 {len(out.protected)}건을 막았습니다. "
            "이런 값은 맞춤법 검사로 바꾸지 않습니다.")
    return out
