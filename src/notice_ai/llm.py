"""LLM 인터페이스 — 갈아끼울 수 있는 부품.

초안 생성 로직은 '어떤 LLM인지' 몰라도 되게, 공통 인터페이스(generate)만 쓴다.
provider는 환경변수 LLM_PROVIDER로 고른다:
    openai   집 테스트 (OpenAI GPT)          — OPENAI_API_KEY 필요
    local    로컬 LLM (OpenAI 호환 서버, 예: Qwen/vLLM/Ollama) — LOCAL_LLM_BASE_URL
    company  회사 GPT 엔터프라이즈(OpenAI 호환이면 그대로) — COMPANY_LLM_* 

셋 다 결국 'OpenAI 호환 채팅 API'라 한 클래스로 처리하고 설정만 바꾼다.
호환이 아닌 회사 API가 나오면 CompatClient 대신 새 구현만 추가하면 된다(로직 불변).
"""

from __future__ import annotations

import os
from functools import lru_cache
from typing import Protocol


class LLM(Protocol):
    def generate(self, system: str, user: str, *, max_tokens: int = 1500) -> str: ...


class OpenAICompatLLM:
    """OpenAI 호환 채팅 API 클라이언트(openai / local / company 공용)."""

    def __init__(self, api_key: str, base_url: str | None, model: str):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url)
        self._model = model

    def generate(self, system: str, user: str, *, max_tokens: int = 1500) -> str:
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ],
            temperature=0.3,          # 공지문은 안정적이어야 하므로 낮게
            max_tokens=max_tokens,
        )
        return resp.choices[0].message.content or ""


@lru_cache(maxsize=1)
def get_llm() -> LLM:
    provider = os.environ.get("LLM_PROVIDER", "openai").lower()

    if provider == "openai":
        key = os.environ.get("OPENAI_API_KEY")
        if not key:
            raise RuntimeError("OPENAI_API_KEY 환경변수가 필요합니다.")
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

    raise RuntimeError(f"알 수 없는 LLM_PROVIDER: {provider}")
