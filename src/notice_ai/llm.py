"""LLM 인터페이스 — 갈아끼울 수 있는 부품.

초안 생성 로직은 '어떤 LLM인지' 몰라도 되게, 공통 인터페이스(generate)만 쓴다.
provider는 환경변수 LLM_PROVIDER로 고른다:
    openai   집 테스트 (OpenAI GPT)          — OPENAI_API_KEY 필요
    local    로컬 LLM (OpenAI 호환 서버, 예: Qwen/vLLM/Ollama) — LOCAL_LLM_BASE_URL
    company  회사 GPT 엔터프라이즈(OpenAI 호환이면 그대로) — COMPANY_LLM_* 

셋 다 결국 'OpenAI 호환 채팅 API'라 한 클래스로 처리하고 설정만 바꾼다.
호환이 아닌 회사 API가 나오면 CompatClient 대신 새 구현만 추가하면 된다(로직 불변).

호출 실패는 LLMError(kind)로 바꿔 올린다 → api.py가 HTTP 상태(502/503/504)와 안내 문구로 돌려준다.
    LLM_TIMEOUT       호출 1번 제한시간(초, 기본 90). 라이브러리 기본값 10분이면 멈춘 호출이 /draft를 붙잡는다
    LLM_MAX_RETRIES   일시 오류·호출 한도 재시도 횟수(기본 1)
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Protocol

LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "90"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "1"))


class LLM(Protocol):
    def generate(self, system: str, user: str, *, max_tokens: int = 1500) -> str: ...


class LLMError(RuntimeError):
    """LLM 호출 실패. kind: timeout | rate_limit | auth | config | error."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


class OpenAICompatLLM:
    """OpenAI 호환 채팅 API 클라이언트(openai / local / company 공용)."""

    def __init__(self, api_key: str, base_url: str | None, model: str):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=LLM_TIMEOUT, max_retries=LLM_MAX_RETRIES)
        self._model = model

    def generate(self, system: str, user: str, *, max_tokens: int = 1500) -> str:
        import openai

        try:
            resp = self._client.chat.completions.create(
                model=self._model,
                messages=[
                    {"role": "system", "content": system},
                    {"role": "user", "content": user},
                ],
                temperature=0.3,          # 공지문은 안정적이어야 하므로 낮게
                max_tokens=max_tokens,
            )
        except openai.APITimeoutError as e:
            raise LLMError("timeout", f"LLM 응답이 {LLM_TIMEOUT:.0f}초 안에 오지 않았습니다.") from e
        except openai.RateLimitError as e:
            raise LLMError("rate_limit", f"LLM 호출 한도(요금·분당 한도)를 넘었습니다: {_brief(e)}") from e
        except (openai.AuthenticationError, openai.PermissionDeniedError) as e:
            # 메시지에 키 일부가 들어 있을 수 있어 옮기지 않는다
            raise LLMError("auth", "LLM API 키가 틀렸거나 권한이 없습니다. 서버 설정을 확인하세요.") from e
        except openai.APIError as e:
            raise LLMError("error", f"LLM 호출에 실패했습니다({type(e).__name__}): {_brief(e)}") from e
        return resp.choices[0].message.content or ""


def _brief(e: Exception) -> str:
    return " ".join(str(getattr(e, "message", "") or e).split())[:120]


@lru_cache(maxsize=1)
def get_llm() -> LLM:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()

    if provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise LLMError("config", "OPENAI_API_KEY 환경변수가 필요합니다.")
        model = os.environ.get("OPENAI_MODEL", "gpt-4o-mini")
        return OpenAICompatLLM(key, None, model)

    if provider == "local":
        # 로컬 OpenAI 호환 서버(vLLM/Ollama 등). 키는 보통 아무 값.
        base = os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:8001/v1")
        model = os.environ.get("LOCAL_LLM_MODEL", "qwen2.5")
        return OpenAICompatLLM(os.environ.get("LOCAL_LLM_KEY", "sk-local"), base, model)

    if provider == "company":
        key = os.environ.get("COMPANY_LLM_KEY", "")
        base = os.environ.get("COMPANY_LLM_BASE_URL")  # 회사 엔드포인트
        model = os.environ.get("COMPANY_LLM_MODEL", "gpt-4o")
        return OpenAICompatLLM(key, base, model)

    raise LLMError("config", f"알 수 없는 LLM_PROVIDER: {provider}")


def settings() -> dict:
    """/health?deep=true용 LLM 설정 요약. 실제 호출은 하지 않는다(비용)."""
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()
    try:
        llm = get_llm()
    except LLMError as e:
        return {"provider": provider, "configured": False, "error": str(e)}
    return {"provider": provider, "model": getattr(llm, "_model", ""), "configured": True,
            "timeout": LLM_TIMEOUT, "max_retries": LLM_MAX_RETRIES}
