"""환경설정 한 곳에 모음.

필수(검색·색인):
    OPENSEARCH_ENDPOINT   예: https://search-notice-draft-ai-xxxx.ap-northeast-2.es.amazonaws.com
    OPENSEARCH_USER       FGAC 마스터 사용자(기본 admin)
    OPENSEARCH_PASSWORD   FGAC 마스터 비밀번호

선택(벡터 검색을 켤 때만):
    AWS_REGION            기본 ap-northeast-2
    BEDROCK_EMBED_MODEL   기본 cohere.embed-multilingual-v3
    BEDROCK_LLM_MODEL     HyDE용 Claude 모델 ID(있을 때만 HyDE 사용)
    BEDROCK_RERANK_MODEL  기본 cohere.rerank-v3-5:0
"""

from __future__ import annotations

import os

INDEX_NAME = os.environ.get("NOTICE_INDEX", "notices")
EMBED_DIM = int(os.environ.get("EMBED_DIM", "1024"))  # Cohere multilingual v3
AWS_REGION = os.environ.get("AWS_REGION", "ap-northeast-2")


def endpoint() -> str:
    ep = os.environ.get("OPENSEARCH_ENDPOINT")
    if not ep:
        raise RuntimeError("OPENSEARCH_ENDPOINT 환경변수가 필요합니다.")
    return ep.rstrip("/")


def basic_auth() -> tuple[str, str]:
    user = os.environ.get("OPENSEARCH_USER", "admin")
    pw = os.environ.get("OPENSEARCH_PASSWORD")
    if not pw:
        raise RuntimeError("OPENSEARCH_PASSWORD 환경변수가 필요합니다.")
    return user, pw


def has_bedrock_embed() -> bool:
    # 자격증명 자체는 boto3가 알아서 찾음. 리전만 있으면 시도 가능.
    return bool(AWS_REGION)
