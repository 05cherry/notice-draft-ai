"""환경설정 한 곳에 모음.

필수(검색·색인):
    OPENSEARCH_ENDPOINT   예: https://search-notice-draft-ai-xxxx.ap-northeast-2.es.amazonaws.com
    OPENSEARCH_USER       FGAC 마스터 사용자(기본 admin)
    OPENSEARCH_PASSWORD   FGAC 마스터 비밀번호

선택(인덱스):
    NOTICE_ALIAS          기본 notices_live. 검색·색인이 보는 별칭. 사전을 갱신할 때 새 인덱스를
                          만들고 이 별칭만 돌린다(원자적). 만들기 전에는 NOTICE_INDEX로 돈다.
    NOTICE_INDEX          기본 notices_v3. 별칭이 없을 때 쓰는 이름이자, 새 인덱스 이름의 앞머리.
                          옛 인덱스로 되돌리려면 NOTICE_INDEX=notices (롤백용으로 보존 중)

선택(사전 자동 갱신):
    DICT_AUTO_REBUILD     기본 1(켬). 신규 상장으로 사전이 뒤처지면 새 인덱스로 옮긴다. 0이면 표시만.
    DICT_REBUILD_MIN_SEC  기본 21600(6시간). 이 간격보다 자주는 안 돈다(연속 상장 때 몰아치기 방지).

선택(벡터 검색을 켤 때만):
    AWS_REGION            기본 ap-northeast-2
    BEDROCK_EMBED_MODEL   기본 amazon.titan-embed-text-v2:0 (서울 리전 온디맨드. cohere v3는 서울에 없음)
    BEDROCK_LLM_MODEL     HyDE용 Claude 모델 ID(있을 때만 HyDE 사용)
    BEDROCK_RERANK_MODEL  기본 cohere.rerank-v3-5:0
"""

from __future__ import annotations

import os

INDEX_NAME = os.environ.get("NOTICE_INDEX", "notices_v3")
INDEX_ALIAS = os.environ.get("NOTICE_ALIAS", "notices_live").strip()


def _flag(name: str, default: bool) -> bool:
    v = os.environ.get(name)
    return default if v is None else v.strip().lower() in {"1", "true", "yes", "on", "t", "y"}


def auto_rebuild() -> bool:
    """신규 상장 감지 시 재색인까지 자동으로 할지. 환경변수를 매번 읽어 재시작 없이 끌 수 있다."""
    return _flag("DICT_AUTO_REBUILD", True)


def rebuild_min_sec() -> float:
    try:
        return max(0.0, float(os.environ.get("DICT_REBUILD_MIN_SEC", "21600")))
    except ValueError:
        return 21600.0
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))  # Cohere multilingual v3
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-2")


class ConfigError(RuntimeError):
    """필수 환경변수가 없음. API는 503과 안내 문구로 돌려준다."""


def endpoint() -> str:
    ep = os.environ.get("OPENSEARCH_ENDPOINT")
    if not ep:
        raise ConfigError("OPENSEARCH_ENDPOINT 환경변수가 필요합니다.")
    return ep.rstrip("/")


def basic_auth() -> tuple[str, str]:
    user = os.environ.get("OPENSEARCH_USER", "admin")
    pw = os.environ.get("OPENSEARCH_PASSWORD")
    if not pw:
        raise ConfigError("OPENSEARCH_PASSWORD 환경변수가 필요합니다.")
    return user, pw


def has_bedrock_embed() -> bool:
    # 자격증명 자체는 boto3가 알아서 찾음. 리전만 있으면 시도 가능.
    return bool(AWS_REGION)
