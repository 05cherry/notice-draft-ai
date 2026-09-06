"""Bedrock 임베딩 — Cohere Embed Multilingual v3 (선택 기능).

벡터 검색을 켤 때만 쓴다. 색인은 search_document, 검색어는 search_query.
Bedrock을 안 쓸 거면 이 모듈을 호출하지 않으면 되고, search는 BM25로만 동작한다.

한국어 성능을 더 끌어올리려면 이 자리에 로컬 BGE-M3(sentence-transformers)를
끼워도 된다. 인터페이스(embed_documents/embed_query)만 맞추면 search는 그대로 동작.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

from notice_ai import config

EMBED_MODEL = os.environ.get("BEDROCK_EMBED_MODEL", "cohere.embed-multilingual-v3")


@lru_cache(maxsize=1)
def _client():
    import boto3

    return boto3.client("bedrock-runtime", region_name=config.AWS_REGION)


def _embed(texts: list[str], input_type: str) -> list[list[float]]:
    body = json.dumps(
        {"texts": texts, "input_type": input_type, "embedding_types": ["float"]}
    )
    resp = _client().invoke_model(modelId=EMBED_MODEL, body=body)
    payload = json.loads(resp["body"].read())
    embs = payload["embeddings"]
    return embs["float"] if isinstance(embs, dict) else embs


def embed_documents(texts: list[str]) -> list[list[float]]:
    return _embed(texts, "search_document")


def embed_query(text: str) -> list[float]:
    return _embed([text], "search_query")[0]
