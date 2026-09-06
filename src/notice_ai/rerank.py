"""리랭킹(선택) — Bedrock Rerank API (Cohere Rerank 3.5)."""

from __future__ import annotations

import os
from functools import lru_cache

from notice_ai import config

RERANK_MODEL = os.environ.get("BEDROCK_RERANK_MODEL", "cohere.rerank-v3-5:0")


@lru_cache(maxsize=1)
def _client():
    import boto3

    return boto3.client("bedrock-agent-runtime", region_name=config.AWS_REGION)


def rerank(query: str, documents: list[str], top_n: int) -> list[tuple[int, float]]:
    if not documents:
        return []
    arn = f"arn:aws:bedrock:{config.AWS_REGION}::foundation-model/{RERANK_MODEL}"
    resp = _client().rerank(
        queries=[{"type": "TEXT", "textQuery": {"text": query}}],
        sources=[
            {"type": "INLINE", "inlineDocumentSource": {"type": "TEXT", "textDocument": {"text": d}}}
            for d in documents
        ],
        rerankingConfiguration={
            "type": "BEDROCK_RERANKING_MODEL",
            "bedrockRerankingConfiguration": {
                "modelConfiguration": {"modelArn": arn},
                "numberOfResults": min(top_n, len(documents)),
            },
        },
    )
    return [(r["index"], r["relevanceScore"]) for r in resp["results"]]
