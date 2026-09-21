"""초안 평가 agent — 생성된 초안을 또 다른 LLM이 채점한다.

기준 5개(담당자 지침 기반), 각 1~5점 + 한 줄 코멘트 + 종합 판정.
평가는 정확도가 중요하므로 더 똑똑한 모델을 쓸 수 있게 EVAL_* 환경변수로 분리한다.
코드 검증(factcheck)이 잡는 사실 오류와 달리, 여기서는 의미·문체·완결성을 본다.

환경변수(선택):
    EVAL_PROVIDER  기본은 LLM_PROVIDER 따라감(openai)
    EVAL_MODEL     기본 gpt-4o (생성용 mini보다 똑똑하게)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import asdict, dataclass, field
from datetime import date

from notice_ai.llm import LLM, for_role

_CRITERIA = [
    ("format", "형식 준수: 공지다운 항목 구성과 [필수 항목]을 갖췄는가"),
    ("tone", "최신 어투: 인사말·끝인사·회원 호칭이 [참고 공지 발췌]의 최신 스타일과 일관적인가"),
    ("caution_fit", "사유-유의사항 일치·완결성: 유의사항과 안내 내용이 이번 사유·유형에 맞고 빠진 것이 없는가"),
    ("no_fabrication", "지어내지 않음: 요청 정보에 없는 사실(상태·일정·수치·기관 등)을 임의로 채우거나 덧붙이지 않았는가"),
    ("fixed_values", "입력값 충실도: 가상자산·티커·날짜·대상 등 요청 값이 정확히 반영되고 왜곡되지 않았는가"),
]
# 이 기준이 낮으면 사실 문제라 반드시 수정한다. 나머지는 합계로만 반영.
CRITICAL = ("no_fabrication", "fixed_values")
PASS_TOTAL = 18        # 25점 만점 중 이 미만이면 수정
CRITICAL_MIN = 3       # 핵심 기준이 이 미만이면 수정

_SYSTEM = (
    "너는 가상자산 거래소 공지 품질 평가자다. 주어진 '요청 정보'와 '생성된 초안'을 보고 "
    "아래 5개 기준을 각각 1~5점으로 채점하고 한 줄 코멘트를 단다. "
    "점수는 냉정하게. 문제가 있으면 낮게 준다.\n"
    + "\n".join(f"- {k}: {desc}" for k, desc in _CRITERIA)
    + "\n\n요일은 절대 직접 계산하지 않는다. '[요일 정보]'로 주어진 값만 정답으로 삼고, "
    "주어지지 않았으면 요일의 정오를 문제 삼지 않는다.\n"
    "요청 정보에 없는 값을 [확인 필요], [조항 확인 필요], [네트워크 확인 필요]처럼 남긴 것은 "
    "규칙에 따른 올바른 처리이므로 어떤 기준에서도 감점하지 않는다.\n"
    "요청 정보가 '(카테고리 파트)'로 나뉘어 있으면 파트마다 값이 다른 것이 정상이다(예: 파트별 대상 가상자산).\n"
    "'[코드 검증 결과]'는 규칙 기반 검사 결과다. 참고하되 그대로 옮기지 말고 초안을 직접 읽고 판단한다.\n"
    + "\n반드시 아래 JSON 형식으로만 답한다(설명·마크다운 금지):\n"
    '{"scores":{"format":N,"tone":N,"caution_fit":N,"no_fabrication":N,"fixed_values":N},'
    '"comments":{"format":"...","tone":"...","caution_fit":"...","no_fabrication":"...","fixed_values":"..."},'
    '"verdict":"통과|검토필요|실패","summary":"한 줄 총평"}'
)


@dataclass
class Evaluation:
    scores: dict = field(default_factory=dict)
    comments: dict = field(default_factory=dict)
    verdict: str = ""
    summary: str = ""
    total: int = 0
    error: str | None = None

    def needs_revision(self) -> bool:
        """평가 기준 이하인가. 평가 자체가 실패했으면 평가를 근거로 수정하지 않는다."""
        if self.error:
            return False
        if self.verdict == "실패" or self.total < PASS_TOTAL:
            return True
        return any(self.scores.get(k, 5) < CRITICAL_MIN for k in CRITICAL)

    def problems(self) -> list[str]:
        """수정 프롬프트에 넘길 지적 사항(3점 이하 기준의 코멘트)."""
        return [f"[{k}] {self.comments.get(k, '')}" for k, v in self.scores.items()
                if v <= 3 and self.comments.get(k)]

    def to_dict(self) -> dict:
        return asdict(self)


def _parse_json(text: str) -> dict:
    """```json 펜스 제거 후 파싱. 앞뒤 잡담이 붙어도 첫 { ~ 마지막 } 만 본다."""
    cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
    if not cleaned.startswith("{"):
        s, e = cleaned.find("{"), cleaned.rfind("}")
        if s != -1 and e > s:
            cleaned = cleaned[s:e + 1]
    return json.loads(cleaned)


def _to_int(v) -> int | None:
    try:
        return max(1, min(5, int(float(v))))
    except (TypeError, ValueError):
        return None


_DATE_RE = re.compile(r"(\d{4})[-./](\d{1,2})[-./](\d{1,2})")
_WEEKDAYS = "월화수목금토일"


def _weekday_note(answers: dict) -> str:
    """요청 날짜의 요일을 코드로 계산해 알려준다.

    평가 LLM이 요일을 암산하다 틀려 멀쩡한 초안의 고정값 점수를 깎는 일이 있어,
    정답을 미리 주입한다.
    """
    notes = []
    for k, v in answers.items():
        m = _DATE_RE.search(str(v))
        if not m:
            continue
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        notes.append(f"- {k}({m.group(0)})은(는) '{_WEEKDAYS[d.weekday()]}'요일이다.")
    if not notes:
        return ""
    return "\n[요일 정보 — 코드로 계산한 정답이므로 그대로 신뢰할 것]\n" + "\n".join(notes)


def evaluate(
    answers: dict,
    draft: str,
    *,
    llm: LLM | None = None,
    notice_type: str = "",
    sections: list[str] | tuple[str, ...] = (),
    code_issues: list[str] | tuple[str, ...] = (),
    reference_excerpt: str = "",
) -> Evaluation:
    if not draft.strip():
        return Evaluation(verdict="실패", summary="초안이 비어있음", error="empty draft")

    req = "\n".join(f"- {k}: {v}" for k, v in answers.items() if v)
    parts = []
    if notice_type:
        parts.append(f"[공지 유형] {notice_type}")
    parts.append(f"[요청 정보]\n{req}\n{_weekday_note(answers)}")
    if sections:
        parts.append("[필수 항목] " + " / ".join(sections))
    if code_issues:
        parts.append("[코드 검증 결과]\n" + "\n".join(f"- {i}" for i in code_issues))
    if reference_excerpt:
        parts.append(f"[참고 공지 발췌 — 문체 비교용, 사실값은 가려짐]\n{reference_excerpt}")
    parts.append(f"[생성된 초안]\n{draft}")

    try:
        raw = (llm or for_role("evaluate")).generate(_SYSTEM, "\n\n".join(parts), max_tokens=800)
        data = _parse_json(raw)
    except Exception as e:  # 평가 LLM 실패해도 파이프라인 전체는 죽지 않게
        return Evaluation(verdict="평가불가", summary=str(e)[:80], error=str(e))

    scores = {k: s for k, v in (data.get("scores") or {}).items() if (s := _to_int(v)) is not None}
    return Evaluation(
        scores=scores,
        comments=data.get("comments", {}) or {},
        verdict=str(data.get("verdict", "")),
        summary=str(data.get("summary", "")),
        total=sum(scores.values()),
    )
