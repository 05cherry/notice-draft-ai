"""LLM 인터페이스 — 갈아끼울 수 있는 부품.

초안 생성 로직은 '어떤 LLM인지' 몰라도 되게, 공통 인터페이스(generate)만 쓴다.

부르는 곳마다 하는 일이 달라서 역할(role)별로 모델을 따로 고른다 — for_role(역할).
    extract    요청문에서 값 뽑기. 짧은 JSON 하나라 싼 모델로 충분
    draft      초안 생성·수정. 품질이 곧 결과물
    evaluate   초안 평가. 생성보다 똑똑한 모델을 쓰라고 원래부터 따로 뒀다
역할별로 {EXTRACT,DRAFT,EVAL}_MODEL · {EXTRACT,DRAFT,EVAL}_PROVIDER 를 줄 수 있고,
없으면 (구) OPENAI_MODEL 등과 LLM_PROVIDER 를 그대로 물려받는다(쓰던 설정이 안 깨진다).

provider는 환경변수 LLM_PROVIDER로 고른다:
    openai   집 테스트 (OpenAI GPT)          — OPENAI_API_KEY 필요
    local    로컬 LLM (OpenAI 호환 서버, 예: Qwen/vLLM/Ollama) — LOCAL_LLM_BASE_URL
    company  회사 GPT 엔터프라이즈(OpenAI 호환이면 그대로) — COMPANY_LLM_* 

셋 다 결국 'OpenAI 호환 채팅 API'라 한 클래스로 처리하고 설정만 바꾼다.
호환이 아닌 회사 API가 나오면 CompatClient 대신 새 구현만 추가하면 된다(로직 불변).

호출 실패는 LLMError(kind)로 바꿔 올린다 → api.py가 HTTP 상태(502/503/504)와 안내 문구로 돌려준다.
    LLM_TIMEOUT       호출 1번 제한시간(초, 기본 90). 라이브러리 기본값 10분이면 멈춘 호출이 /draft를 붙잡는다
    LLM_MAX_RETRIES   일시 오류·호출 한도 재시도 횟수(기본 1)

키가 거부될 때(401/403)는 원인이 여럿이라(키 오타·붙여넣기 오염·만료, 모델 권한 없음, 프로젝트 제한)
안내 문구에 상태코드·API 오류코드·모델명·키 모양을 같이 담는다. 키 값 자체는 절대 담지 않는다.
실제로 통하는지는 check_auth()로 확인한다(/health?deep=true. 모델 목록 조회라 토큰 비용 없음).
"""

from __future__ import annotations

import logging
import os
from functools import lru_cache
from typing import Protocol

logger = logging.getLogger(__name__)

LLM_TIMEOUT = float(os.environ.get("LLM_TIMEOUT", "90"))
LLM_MAX_RETRIES = int(os.environ.get("LLM_MAX_RETRIES", "1"))

# provider별 키 환경변수 이름 — 안내 문구에서 "무엇을 고쳐야 하는지" 가리키는 데 쓴다.
KEY_ENV = {"openai": "OPENAI_API_KEY", "local": "LOCAL_LLM_KEY", "company": "COMPANY_LLM_KEY"}

ROLES = ("extract", "draft", "evaluate")

# 역할 → 환경변수 이름. evaluate 는 EVALUATE_ 가 아니라 EVAL_ 이다(쓰던 이름 그대로).
_ROLE_PROVIDER_ENV = {"extract": "EXTRACT_PROVIDER", "draft": "DRAFT_PROVIDER", "evaluate": "EVAL_PROVIDER"}
_ROLE_MODEL_ENV = {"extract": "EXTRACT_MODEL", "draft": "DRAFT_MODEL", "evaluate": "EVAL_MODEL"}

# (구) provider별 모델 환경변수. 역할별 값이 없을 때 물려받는다.
_PROVIDER_MODEL_ENV = {"openai": "OPENAI_MODEL", "local": "LOCAL_LLM_MODEL", "company": "COMPANY_LLM_MODEL"}
_PROVIDER_DEFAULT = {"openai": "gpt-4o-mini", "local": "qwen2.5", "company": "gpt-4o"}
# 평가는 생성보다 똑똑한 모델을 쓰려고 일부러 따로 둔 자리라 provider 모델을 물려받지 않는다.
# (local 만 예외 — 로컬 서버는 모델을 하나만 띄워 두는 경우가 많다.)
_EVAL_DEFAULT = {"openai": "gpt-4o", "local": "qwen2.5", "company": "gpt-4o"}


class LLM(Protocol):
    def generate(self, system: str, user: str, *, max_tokens: int = 1500) -> str: ...


class LLMError(RuntimeError):
    """LLM 호출 실패. kind: timeout | rate_limit | auth | config | error."""

    def __init__(self, kind: str, message: str):
        super().__init__(message)
        self.kind = kind


def api_key(provider: str, *, default: str = "") -> str:
    """provider의 키를 환경변수에서 읽어 다듬는다. 필수인데 없으면 LLMError('config').

    대시보드(Render 등)에 붙여 넣을 때 따옴표·공백·줄바꿈이 같이 들어가는 일이 잦고,
    그러면 키는 맞는데 401만 돌아온다. 키에는 원래 이런 문자가 없으므로 떼어 내고 남긴다.
    """
    env = KEY_ENV.get(provider, "")
    raw = os.environ.get(env, "") if env else ""
    key = raw.strip().strip("\"'").strip()
    if key != raw:
        logger.warning("%s 값에 따옴표·공백·줄바꿈이 섞여 있어 떼어 내고 씁니다(길이 %d→%d).",
                       env, len(raw), len(key))
    if key:
        return key
    if default:
        return default
    raise LLMError("config", f"{env} 환경변수가 필요합니다.")


def key_shape(key: str) -> str:
    """키 값을 드러내지 않고 모양만 알려 준다(로그·안내 문구용)."""
    if not key:
        return "비어 있음"
    prefix = next((p for p in ("sk-proj-", "sk-svcacct-", "sk-admin-", "sk-") if key.startswith(p)), "")
    return f"길이 {len(key)}자, {prefix}로 시작" if prefix else f"길이 {len(key)}자, sk-로 시작하지 않음"


class OpenAICompatLLM:
    """OpenAI 호환 채팅 API 클라이언트(openai / local / company 공용)."""

    def __init__(self, api_key: str, base_url: str | None, model: str, *, key_env: str = ""):
        from openai import OpenAI

        self._client = OpenAI(api_key=api_key, base_url=base_url, timeout=LLM_TIMEOUT, max_retries=LLM_MAX_RETRIES)
        self._model = model
        self._key_env = key_env or "LLM 키"
        self._key_shape = key_shape(api_key)

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
        except openai.AuthenticationError as e:
            # 키가 거부됨(401). 오류 메시지에는 키 일부가 들어 있을 수 있어 코드만 옮긴다.
            raise LLMError("auth", self._auth_message(e)) from e
        except openai.PermissionDeniedError as e:
            # 키는 통했지만 이 모델·프로젝트를 쓸 수 없음(403). 키를 바꿔도 소용없는 경우가 많다.
            raise LLMError("auth", self._denied_message(e)) from e
        except openai.APIError as e:
            raise LLMError("error", f"LLM 호출에 실패했습니다({type(e).__name__}): {_brief(e)}") from e
        return resp.choices[0].message.content or ""

    def _auth_message(self, e: Exception) -> str:
        return (f"LLM API 키가 거부됐습니다(401{_code(e)}). 서버의 {self._key_env} 값을 확인하세요 "
                f"— 지금 쓰는 키는 {self._key_shape}. 키를 새로 발급했다면 서버를 다시 배포해야 반영됩니다.")

    def _denied_message(self, e: Exception) -> str:
        return (f"LLM 키에 '{self._model}' 모델을 쓸 권한이 없습니다(403{_code(e)}). "
                f"키 자체는 살아 있으니 바꿔도 소용없고, OpenAI 프로젝트의 모델 권한 또는 모델 이름을 확인하세요.")


def _code(e: Exception) -> str:
    """API가 준 오류 코드·타입(invalid_api_key, model_not_found 등). 키 값은 들어 있지 않다."""
    code = getattr(e, "code", None) or getattr(e, "type", None)
    rid = getattr(e, "request_id", None)
    return f" {code}" + (f", request {rid}" if rid else "") if code else ""


def _brief(e: Exception) -> str:
    return " ".join(str(getattr(e, "message", "") or e).split())[:120]


def provider_for(role: str) -> str:
    """역할이 쓸 provider. {역할}_PROVIDER → LLM_PROVIDER → openai."""
    env = _ROLE_PROVIDER_ENV.get(role, "")
    return (os.environ.get(env) or os.environ.get("LLM_PROVIDER") or "openai").strip().lower()


def model_for(role: str, provider: str | None = None) -> str:
    """역할이 쓸 모델. {역할}_MODEL → (구) provider별 모델 → 기본값.

    평가만 provider 모델(OPENAI_MODEL 등)을 물려받지 않는다. 생성보다 똑똑한 모델을 쓰려고
    일부러 따로 둔 자리이기 때문이고, 나누기 전에도 그렇게 동작했다.
    """
    provider = provider or provider_for(role)
    if value := os.environ.get(_ROLE_MODEL_ENV.get(role, ""), "").strip():
        return value
    if role == "evaluate":
        if provider == "local" and (value := os.environ.get("LOCAL_LLM_MODEL", "").strip()):
            return value
        return _EVAL_DEFAULT.get(provider, "gpt-4o")
    if value := os.environ.get(_PROVIDER_MODEL_ENV.get(provider, ""), "").strip():
        return value
    return _PROVIDER_DEFAULT.get(provider, "gpt-4o-mini")


def _base_url(provider: str) -> str | None:
    if provider == "local":
        return os.environ.get("LOCAL_LLM_BASE_URL", "http://localhost:8001/v1")
    if provider == "company":
        return os.environ.get("COMPANY_LLM_BASE_URL")   # 회사 엔드포인트
    return None


@lru_cache(maxsize=len(ROLES))
def for_role(role: str) -> LLM:
    """역할에 맞는 LLM. 역할마다 모델·provider를 따로 고를 수 있다.

    부르는 곳마다 하는 일이 다르다 — 짧은 JSON 하나 뽑는 추출과 공지 한 편을 쓰는 초안 생성에
    같은 모델을 매어 둘 이유가 없다. 바꾸는 값이 싸야 실제로 바꿔 본다.
    """
    if role not in ROLES:
        raise LLMError("config", f"알 수 없는 역할: {role}. 가능: {', '.join(ROLES)}")
    provider = provider_for(role)
    if provider not in KEY_ENV:
        env = _ROLE_PROVIDER_ENV[role] if os.environ.get(_ROLE_PROVIDER_ENV[role]) else "LLM_PROVIDER"
        raise LLMError("config", f"알 수 없는 {env}: {provider}. 가능: {', '.join(KEY_ENV)}")
    default_key = "sk-local" if provider == "local" else ""   # 로컬 서버는 키를 안 본다
    return OpenAICompatLLM(api_key(provider, default=default_key), _base_url(provider),
                           model_for(role, provider), key_env=KEY_ENV[provider])


def get_llm() -> LLM:
    """(구) 이름. 초안 생성용 LLM."""
    return for_role("draft")


def check_auth(llm: LLM | None = None) -> dict:
    """키가 실제로 통하는지 확인한다. 모델 목록 조회라 토큰 비용은 없다.

    설정만 보는 settings()로는 '키가 있다'까지만 알 수 있어서, 키가 틀린 줄 모르고
    /draft를 불러야 502를 보게 된다. 여기서 미리 잡는다.
    """
    import openai

    try:
        client = (llm or get_llm())
    except LLMError as e:
        return {"ok": False, "kind": e.kind, "error": str(e)}
    if not isinstance(client, OpenAICompatLLM):
        return {"ok": None, "kind": "skip", "error": "확인 방법이 없는 LLM 구현입니다."}
    try:
        client._client.models.list()
    except openai.AuthenticationError as e:
        return {"ok": False, "kind": "auth", "error": client._auth_message(e)}
    except openai.PermissionDeniedError as e:
        # 목록 조회만 막아 둔 키도 있어 초안 생성은 될 수 있다. 단정하지 않는다.
        return {"ok": None, "kind": "auth",
                "error": f"모델 목록 조회가 거부됐습니다(403{_code(e)}). 초안 생성은 될 수도 있습니다."}
    except Exception as e:
        return {"ok": None, "kind": "error", "error": f"확인하지 못했습니다({type(e).__name__}): {_brief(e)}"}
    return {"ok": True}


def settings() -> dict:
    """/health?deep=true용 LLM 설정 요약. 실제 호출은 하지 않는다(비용).

    provider·model 은 초안 생성 기준이다(예전 응답과 같은 자리). 역할마다 다를 수 있으므로
    roles 에 셋을 다 담는다 — 화면에서 '추출은 싼 모델, 평가는 큰 모델'을 눈으로 확인하려면
    이게 있어야 한다.
    """
    roles = {r: {"provider": provider_for(r), "model": model_for(r)} for r in ROLES}
    try:
        llm = for_role("draft")
    except LLMError as e:
        return {"provider": provider_for("draft"), "configured": False, "error": str(e), "roles": roles}
    return {"provider": provider_for("draft"), "model": getattr(llm, "_model", ""), "configured": True,
            "key": getattr(llm, "_key_shape", ""), "roles": roles,
            "timeout": LLM_TIMEOUT, "max_retries": LLM_MAX_RETRIES}
