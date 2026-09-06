"""임베딩 백필 — embedding 없는 문서를 Bedrock으로 채운다(선택 기능).

수집과 분리. 벡터 검색을 켜기로 했을 때만 돌린다.
재실행 안전: embedding 필드가 없는 문서만 처리.
"""

from __future__ import annotations

import logging

from notice_ai import config
from notice_ai.embeddings import EMBED_MODEL, embed_documents
from notice_ai.opensearch_client import get_client

logger = logging.getLogger(__name__)

_INDEX_TEXT_LIMIT = 2000


def _pending(client, size: int) -> list[dict]:
    q = {
        "size": size,
        "query": {"bool": {"must_not": [{"exists": {"field": "embedding"}}]}},
        "_source": ["title", "raw_text"],
    }
    res = client.search(index=config.INDEX_NAME, body=q)
    return res["hits"]["hits"]


def embed_missing(batch: int = 96) -> int:
    client = get_client()
    total = 0
    while True:
        hits = _pending(client, batch)
        if not hits:
            break
        texts = [
            f"{h['_source'].get('title','')}\n{h['_source'].get('raw_text','')[:_INDEX_TEXT_LIMIT]}"
            for h in hits
        ]
        vecs = embed_documents(texts)
        for h, v in zip(hits, vecs):
            client.update(
                index=config.INDEX_NAME,
                id=h["_id"],
                body={"doc": {"embedding": v, "embed_model": EMBED_MODEL}},
            )
        client.indices.refresh(index=config.INDEX_NAME)
        total += len(hits)
        logger.info("임베딩 %d건 누적", total)
    return total
