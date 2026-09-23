"""임베딩 백필 — embedding 없는 문서를 Bedrock으로 채운다(선택 기능).

수집과 분리. 벡터 검색을 켜기로 했을 때만 돌린다.
재실행 안전: embedding 필드가 없는 문서만 처리. 중간에 멈춰도 다시 돌리면 이어서 한다.
"""

from __future__ import annotations

import logging

from opensearchpy import helpers

from notice_ai import config, index_ref
from notice_ai.embeddings import EMBED_MODEL, embed_documents
from notice_ai.opensearch_client import get_client

logger = logging.getLogger(__name__)

_INDEX_TEXT_LIMIT = 2000


def _pending(client, size: int, skip_ids: set[str]) -> list[dict]:
    must_not = [{"exists": {"field": "embedding"}}]
    if skip_ids:
        must_not.append({"ids": {"values": sorted(skip_ids)}})
    q = {"size": size, "query": {"bool": {"must_not": must_not}}, "_source": ["title", "raw_text"]}
    res = client.search(index=index_ref.target(), body=q)
    return res["hits"]["hits"]


def embed_missing(batch: int = 50, limit: int | None = None) -> int:
    """limit: 이 건수까지만(시험용). 반환값: 새로 임베딩한 문서 수."""
    client = get_client()
    total, failed = 0, set()
    while limit is None or total < limit:
        size = batch if limit is None else min(batch, limit - total)
        hits = _pending(client, size, failed)
        if not hits:
            break
        actions = []
        for h in hits:
            text = f"{h['_source'].get('title', '')}\n{(h['_source'].get('raw_text') or '')[:_INDEX_TEXT_LIMIT]}"
            try:
                vec = embed_documents([text])[0]
            except Exception as e:   # 한 건 실패로 전체를 멈추지 않는다. 다음 조회에서 제외해 무한 반복 방지
                failed.add(h["_id"])
                logger.warning("임베딩 실패 %s: %s", h["_id"], str(e)[:120])
                continue
            actions.append({"_op_type": "update", "_index": index_ref.target(), "_id": h["_id"],
                            "doc": {"embedding": vec, "embed_model": EMBED_MODEL}})
        if actions:
            helpers.bulk(client, actions, refresh=True)
        total += len(actions)
        logger.info("임베딩 %d건 누적 (실패 %d건)", total, len(failed))
    return total
