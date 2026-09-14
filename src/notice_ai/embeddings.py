"""Bedrock 임베딩(선택 기능) — 기본 Amazon Titan Text Embeddings V2.

벡터 검색을 켤 때만 쓴다. search는 임베딩이 없으면 BM25로만 동작한다.

모델 선택(2026-09-14 서울 리전 확인):
  - amazon.titan-embed-text-v2:0  서울 온디맨드, 권한·약관 OK → 기본값. 데이터가 서울 밖으로 나가지 않는다.
  - cohere.embed-multilingual-v3  서울 리전에 없음(예전 기본값).
  - cohere.embed-v4:0             전역 교차 리전 프로파일(global.cohere.embed-v4:0)로만 가능 + Marketplace 약관 동의 필요.
BEDROCK_EMBED_MODEL 환경변수로 바꿀 수 있다. 차원은 EMBED_DIM(기본 1024, 인덱스 knn_vector와 같아야 함).

한국어 성능을 더 끌어올리려면 이 자리에 로컬 BGE-M3(sentence-transformers)를
끼워도 된다. 인터페이스(embed_documents/embed_query)만 맞추면 search는 그대로 동작.
"""

from __future__ import annotations

import json
import os
import time
from functools import lru_cache

from notice_ai import config

EMBED_MODEL = os.environ.get("BEDROCK_EMBED_MODEL", "amazon.titan-embed-text-v2:0")
_MAX_CHARS = 8000        # Titan V2 입력 한도(8,192토큰/50,000자)보다 넉넉히 작게
_RETRIES = 5


@lru_cache(maxsize=1)
def _client():
    import boto3

    return boto3.client("bedrock-runtime", region_name=config.AWS_REGION)


def _invoke(body: dict) -> dict:
    """호출 제한(ThrottlingException)이면 잠깐 쉬었다 다시 부른다."""
    from botocore.exceptions import ClientError

    for attempt in range(_RETRIES):
        try:
            resp = _client().invoke_model(modelId=EMBED_MODEL, body=json.dumps(body))
            return json.loads(resp["body"].read())
        except ClientError as e:
            if e.response.get("Error", {}).get("Code") != "ThrottlingException" or attempt == _RETRIES - 1:
                raise
            time.sleep(2 ** attempt)
    raise RuntimeError("unreachable")


def _is_titan() -> bool:
    return EMBED_MODEL.startswith("amazon.titan-embed")


def _embed(texts: list[str], input_type: str) -> list[list[float]]:
    texts = [(t or " ")[:_MAX_CHARS] for t in texts]
    if _is_titan():
        # Titan은 한 번에 한 문장. 정규화하면 innerproduct = 코사인 유사도.
        return [_invoke({"inputText": t, "dimensions": config.EMBED_DIM, "normalize": True})["embedding"]
                for t in texts]
    body = {"texts": texts, "input_type": input_type, "embedding_types": ["float"]}
    if "embed-v4" in EMBED_MODEL:
        body["output_dimension"] = config.EMBED_DIM
    embs = _invoke(body)["embeddings"]
    return embs["float"] if isinstance(embs, dict) else embs


def embed_documents(texts: list[str]) -> list[list[float]]:
    return _embed(texts, "search_document")


def embed_query(text: str) -> list[float]:
    return _embed([text], "search_query")[0]
