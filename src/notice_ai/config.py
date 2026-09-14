"""환경설정 한 곳에 모음.

필수(검색·색인):
    OPENSEARCH_ENDPOINT   예: https://search-notice-draft-ai-xxxx.ap-northeast-2.es.amazonaws.com
    OPENSEARCH_USER       FGAC 마스터 사용자(기본 admin)
    OPENSEARCH_PASSWORD   FGAC 마스터 비밀번호

선택(인덱스):
    NOTICE_INDEX          기본 notices_v3 (소문자화 + 조사·어미 필터 + 검색 시 동의어).
                          옛 인덱스로 되돌리려면 NOTICE_INDEX=notices (롤백용으로 보존 중)

선택(벡터 검색을 켤 때만):
    AWS_REGION            기본 ap-northeast-2
    BEDROCK_EMBED_MODEL   기본 amazon.titan-embed-text-v2:0 (서울 리전 온디맨드. cohere v3는 서울에 없음)
    BEDROCK_LLM_MODEL     HyDE용 Claude 모델 ID(있을 때만 HyDE 사용)
    BEDROCK_RERANK_MODEL  기본 cohere.rerank-v3-5:0
"""

from __future__ import annotations

import os

INDEX_NAME = os.environ.get("NOTICE_INDEX", "notices_v3")
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
