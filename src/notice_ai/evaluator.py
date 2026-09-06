"""초안 평가 agent — 생성된 초안을 또 다른 LLM이 채점한다.

기준 5개(담당자 지침 기반), 각 1~5점 + 한 줄 코멘트 + 종합 판정.
평가는 정확도가 중요하므로 더 똑똑한 모델을 쓸 수 있게 EVAL_* 환경변수로 분리한다.

환경변수(선택):
    EVAL_PROVIDER  기본은 LLM_PROVIDER 따라감(openai)
    EVAL_MODEL     기본 gpt-4o (생성용 mini보다 똑똑하게)
"""

from __future__ import annotations

import json
import os
import re
from dataclasses import dataclass, field

from notice_ai.llm import OpenAICompatLLM

_CRITERIA = [
    ("format", "형식 준수: 정해진 출력 형식과 공지다운 항목 구성을 지켰는가"),
    ("tone", "최신 어투: 인사말·끝인사·회원 호칭이 최신 공지 스타일인가"),
    ("caution_fit", "사유-유의사항 일치: 유의사항이 이번 사유에 맞는가(특히 입출금)"),
    ("no_fabrication", "지어내지 않음: 없는 정보를 [확인 필요]로 남기고 임의로 채우지 않았는가"),
    ("fixed_values", "고정값 정확성: 코인·티커·날짜가 이번 요청 값으로 정확히 반영됐는가"),
]

_SYSTEM = (
    "너는 가상자산 거래소 공지 품질 평가자다. 주어진 '요청 정보'와 '생성된 초안'을 보고 "
    "아래 5개 기준을 각각 1~5점으로 채점하고 한 줄 코멘트를 단다. "
    "점수는 냉정하게. 문제가 있으면 낮게 준다.\n"
    + "\n".join(f"- {k}: {desc}" for k, desc in _CRITERIA)
    + "\n\n반드시 아래 JSON 형식으로만 답한다(설명·마크다운 금지):\n"
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


def _eval_llm() -> OpenAICompatLLM:
    """평가용 LLM. 기본은 OpenAI + 더 똑똑한 모델(gpt-4o)."""
    provider = os.environ.get("EVAL_PROVIDER", os.environ.get("LLM_PROVIDER", "openai"))
    if provider == "openai":
        key = os.environ["OPENAI_API_KEY"]
        model = os.environ.get("EVAL_MODEL", "gpt-4o")
        return OpenAICompatLLM(key, None, model)
    if provider == "local":
        base = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:8001/v1")
        model = os.environ.get("EVAL_MODEL", os.environ.get("LOCAL_LLM_MODEL", "qwen2.5"))
        return OpenAICompatLLM(os.environ.get("LOCAL_LLM_KEY", "sk-local"), base, model)
    # company 등
    return OpenAICompatLLM(
        os.environ.get("COMPANY_LLM_KEY", ""),
        os.environ.get("COMPANY_LLM_BASE_URL"),
        os.environ.get("EVAL_MODEL", "gpt-4o"),
    )


def _parse_json(text: str) -> dict:
    """```json 펜스 제거 후 파싱."""
    cleaned = re.sub(r"```(?:json)?|```", "", text).strip()
    return json.loads(cleaned)


def evaluate(answers: dict, draft: str) -> Evaluation:
    if not draft.strip():
        return Evaluation(verdict="실패", summary="초안이 비어있음", error="empty draft")

    req = "\n".join(f"- {k}: {v}" for k, v in answers.items() if v)
    user = f"[요청 정보]\n{req}\n\n[생성된 초안]\n{draft}"

    try:
        raw = _eval_llm().generate(_SYSTEM, user, max_tokens=800)
        data = _parse_json(raw)
    except Exception as e:  # 평가 LLM 실패해도 테스트 전체는 죽지 않게
        return Evaluation(verdict="평가불가", summary=str(e)[:80], error=str(e))

    scores = data.get("scores", {})
    total = sum(int(v) for v in scores.values() if isinstance(v, (int, float)))
    return Evaluation(
        scores=scores,
        comments=data.get("comments", {}),
        verdict=data.get("verdict", ""),
        summary=data.get("summary", ""),
        total=total,
    )
