"""요청문에서 입력값 뽑기 (기능 2의 앞단, #7).

"헤데라(HBAR) 입출금 9/25 15시부터 중단, 네트워크 점검 때문" 처럼 한 줄로 쓴 요청문에서
coins·suspend_at·reason 을 뽑아 입력칸을 미리 채운다. 사람이 같은 내용을 두 번 치지 않게 하는 것이 전부다.

  요청문 + 유형(파트)별 필드 목록 → LLM 1회 → JSON → 기존 정규화·검증 → 제안 목록

대원칙은 그대로다. 여기서 뽑은 값은 **제안일 뿐** 초안에 바로 쓰이지 않는다.
/draft 는 사용자가 확인해 보낸 inputs 만 본다. 그래서 이 모듈이 틀려도 초안의 사실값은 오염되지 않는다.
(뽑는 대상도 사용자가 방금 쓴 요청문 하나뿐이다. 과거 공지는 쳐다보지 않는다.)

뽑은 값도 사람이 직접 친 값과 같은 검사(notice_types.field_problem)를 거친다. 통과 못 하면
fields 가 아니라 rejected 로 내려보내고 화면은 그 칸을 빈 채로 둔다 — 형식이 깨진 값을 채워 주는 것보다 낫다.
"""

from __future__ import annotations

import json
import re
from dataclasses import asdict, dataclass, field
from datetime import datetime
from pathlib import Path

from notice_ai.llm import LLM, get_llm
from notice_ai.notice_types import (
    FIELDS,
    Part,
    display_value,
    field_label,
    field_problem,
    is_temporal,
    is_unknown,
    normalize_inputs,
    now_kst,
    resolve,
)

_PROMPT = (Path(__file__).resolve().parent / "prompts" / "extract.txt").read_text(encoding="utf-8")

# "[조항 확인 필요]" 처럼 대괄호 하나로만 이뤄진 값. 값이 아니라 '값 없음'의 표기다.
_PLACEHOLDER_RE = re.compile(r"^\s*\[[^\]]*\]\s*$")

# 요청문에 '아직 안 정해졌다'는 뜻이 적혀 있는지. 없는데 '미정'을 뽑았다면 지어낸 것이다.
_UNKNOWN_MARK = re.compile(r"미정|미확정|추후|아직|정해지지|안\s*정해|확정되지|모름|미상|TBD", re.I)

MAX_TOKENS = 800           # 필드 10여 개짜리 JSON이면 충분하다
MAX_TEXT = 2000            # 요청문이 이보다 길면 자른다(요청문은 보통 한두 줄)
_WEEKDAYS = "월화수목금토일"

# kind별로 LLM에 알려 줄 형식. notice_types.FIELDS 의 kind 와 짝을 맞춘다.
_FORMAT = {
    "coins": '["한글명(티커)"] 목록',
    "date": '"YYYY-MM-DD"',
    "datetime": '"YYYY-MM-DD HH:MM"',
    "urls": '["https://..."] 목록',
    "text": "문자열",
}


@dataclass
class ExtractOutcome:
    status: str = "ok"                                  # error | ok
    parts: list[dict] = field(default_factory=list)     # 어떤 유형으로 보고 뽑았는지
    fields: list[dict] = field(default_factory=list)    # 채택된 제안 {field,label,value,display,categories}
    rejected: list[dict] = field(default_factory=list)  # 형식이 안 맞아 버린 것 {field,label,value,problem}
    asked: list[str] = field(default_factory=list)      # 뽑아 보려 한 필드 이름(무엇을 못 찾았는지 알 수 있게)
    errors: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)

    def to_dict(self) -> dict:
        return asdict(self)


def _field_lines(parts: list[Part]) -> tuple[list[str], dict[str, list[str]]]:
    """LLM에 줄 항목 설명과, 항목별로 어느 카테고리 파트가 쓰는지."""
    lines: list[str] = []
    owners: dict[str, list[str]] = {}
    for p in parts:
        for name in p.ntype.fields:
            if name in owners:
                owners[name].append(p.category)
                continue
            owners[name] = [p.category]
            f = FIELDS[name]
            note = f" — {f.question}" if f.question else ""
            lines.append(f"- {name} ({field_label(p.ntype, name)}, {_FORMAT.get(f.kind, '문자열')}){note}")
    return lines, owners


def build_prompt(parts: list[Part], text: str, now: datetime) -> tuple[str, str]:
    lines, _ = _field_lines(parts)
    types = " + ".join(f"{p.category} > {p.ntype.label}" for p in parts)
    user = (f"오늘: {now:%Y-%m-%d}({_WEEKDAYS[now.weekday()]})\n"
            f"공지 유형: {types}\n\n"
            "뽑을 항목:\n" + "\n".join(lines) + "\n\n"
            "요청문:\n" + text.strip()[:MAX_TEXT])
    return _PROMPT, user


def parse_json(raw: str) -> dict | None:
    """LLM 답에서 JSON 객체를 꺼낸다. 코드펜스나 앞뒤 설명이 붙어도 읽는다."""
    s = (raw or "").strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s).strip()
    if not s.startswith("{"):
        m = re.search(r"\{.*\}", s, re.S)      # 앞뒤에 말을 붙인 경우
        if not m:
            return None
        s = m.group(0)
    try:
        out = json.loads(s)
    except ValueError:
        return None
    return out if isinstance(out, dict) else None


def _drop_placeholders(raw: dict) -> dict:
    """자리표시자만 든 값을 버린다.

    항목 설명에 그런 문구가 들어 있으면(law_clause 질문의 "없으면 [조항 확인 필요]로 남깁니다")
    LLM이 사람에게 묻는 말을 지시로 읽고 그대로 옮겨 적는다. 실제로 그랬다.

    형식이 틀린 게 아니라 애초에 값이 아니므로 rejected 가 아니라 조용히 버린다.
    사용자가 말한 적 없는 항목을 '형식이 안 맞았다'고 알리면 도움이 아니라 잡음이다.
    빈 채로 두면 초안 단계에서 원래대로 [확인 필요]가 들어간다(drafting._MISSING_HINTS).
    """
    out: dict = {}
    for k, v in raw.items():
        if isinstance(v, str):
            if not _PLACEHOLDER_RE.match(v):
                out[k] = v
        elif isinstance(v, (list, tuple)):
            items = [i for i in v if not (isinstance(i, str) and _PLACEHOLDER_RE.match(i))]
            if items:
                out[k] = items
        else:
            out[k] = v
    return out


def _invented_unknown(value, text: str) -> bool:
    """'미정'류 값인데 요청문에 그런 말이 없으면 지어낸 것이다.

    프롬프트로 두 번 못박았는데도 실제 GPT가 재개 시점이 없는 요청문에 resume_at="미정"을
    채우는 일이 되풀이됐다. 한 요청문에선 안 그러고 다른 요청문에선 그랬다 — 지시 준수는
    확률적이라, 세 번째로 프롬프트를 고치는 것은 같은 실수다. '미정'인지 아닌지는 코드로
    가릴 수 있으므로 코드로 막는다.

    빈 항목과 '미정'은 뜻이 다르다. '미정'은 초안에 '추후 안내'로 못박혀 나가고, 빈 항목은
    사람에게 되물을 여지를 남긴다. 사용자가 말한 적 없는 것을 못박으면 안 된다.
    """
    return is_unknown(value) and not _UNKNOWN_MARK.search(text)


def _collect(parts: list[Part], raw: dict) -> tuple[dict, dict[str, list[str]]]:
    """LLM이 준 값 중 이 유형이 쓰는 항목만, 기존 정규화를 거쳐 모은다."""
    merged: dict = {}
    used: dict[str, list[str]] = {}
    for p in parts:
        allowed = set(p.ntype.fields)
        sub = {k: v for k, v in raw.items() if k in allowed}
        for k, v in normalize_inputs(sub, p.ntype).items():
            if k in merged:
                used[k].append(p.category)
            else:
                merged[k], used[k] = v, [p.category]
    return merged, used


def extract_inputs(
    categories: list[str],
    *,
    text: str = "",
    subtypes: dict[str, str] | None = None,
    llm: LLM | None = None,
    now: datetime | None = None,
) -> ExtractOutcome:
    """요청문에서 이 유형의 입력값을 뽑는다. LLM 1회. 실패해도 예외 대신 빈 결과 + 경고.

    now 를 주지 않으면 KST 기준 지금을 쓴다. 요청문의 '내일'·'오늘'을 푸는 기준이라
    서버가 UTC로 돌면 하루가 어긋난다(now_kst 참고).
    """
    out = ExtractOutcome()
    res = resolve(categories, text=text, subtypes=subtypes)
    out.parts = [p.to_dict() for p in res.parts]
    out.errors = list(res.errors)
    out.warnings = list(res.warnings)
    if res.errors:
        out.status = "error"
        return out
    if not text.strip():
        out.warnings.append("요청문이 비어 있어 뽑을 것이 없습니다.")
        return out

    lines, _ = _field_lines(res.parts)
    out.asked = [ln.split()[1] for ln in lines]
    system, user = build_prompt(res.parts, text, now or now_kst())
    answer = (llm or get_llm()).generate(system, user, max_tokens=MAX_TOKENS)

    raw = parse_json(answer)
    if raw is None:
        out.warnings.append("추출 결과를 읽지 못했습니다(JSON이 아님). 입력칸을 직접 채워 주세요.")
        return out

    merged, used = _collect(res.parts, _drop_placeholders(raw))
    for name, value in merged.items():
        if _invented_unknown(value, text):
            continue        # 자리표시자와 같은 이유로 조용히 버린다(사용자가 말한 적 없는 항목)
        problem = field_problem(name, value)
        label = FIELDS[name].label
        if problem:
            # 형식이 깨진 값을 채워 주면 사람이 고치는 품이 더 든다. 빈 칸으로 두고 이유만 알려 준다.
            out.rejected.append({"field": name, "label": label, "value": value, "problem": problem})
            continue
        out.fields.append({"field": name, "label": label, "value": value,
                           "display": display_value(name, value), "categories": used[name]})
    if any(is_temporal(FIELDS[f["field"]].kind) for f in out.fields):
        out.warnings.append("날짜·일시는 요청문 표현을 해석한 값입니다. 보내기 전에 확인하세요.")
    return out
