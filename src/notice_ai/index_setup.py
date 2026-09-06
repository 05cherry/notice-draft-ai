"""notices 인덱스 생성 — Nori 한국어 분석기 + kNN 벡터.

한 인덱스에 BM25(Nori)와 벡터를 함께 둔다. 그래서 하이브리드가 이 안에서 된다.
tsvector의 한국어 형태소 문제는 Nori가 근본적으로 해결(조사·어미 분리).
"""

from __future__ import annotations

from notice_ai import config
from notice_ai.opensearch_client import get_client


def index_body(dim: int) -> dict:
    return {
        "settings": {
            "index": {"knn": True},
            "analysis": {
                "tokenizer": {
                    "nori_user": {
                        "type": "nori_tokenizer",
                        "decompound_mode": "mixed",  # 복합명사 원형+분해 모두 색인
                    }
                },
                "analyzer": {
                    "korean": {"type": "custom", "tokenizer": "nori_user"}
                },
            },
        },
        "mappings": {
            "properties": {
                "source_url": {"type": "keyword"},   # dedup 키 = 문서 _id로도 사용
                "external_id": {"type": "long"},
                "categories": {"type": "keyword"},    # 배열. term 필터로 포함 검색
                "tickers": {"type": "keyword"},       # 제목에서 추출한 티커(필터용)
                "title": {"type": "text", "analyzer": "korean"},
                "raw_text": {"type": "text", "analyzer": "korean"},
                "published_at": {
                    "type": "date",
                    "format": "yyyy-MM-dd HH:mm:ss||yyyy-MM-dd||yyyy.MM.dd||epoch_millis",
                },
                "modified_at": {
                    "type": "date",
                    "format": "yyyy-MM-dd HH:mm:ss||yyyy-MM-dd||yyyy.MM.dd||epoch_millis",
                },
                "embedding": {
                    "type": "knn_vector",
                    "dimension": dim,
                    "method": {
                        "name": "hnsw",
                        "space_type": "innerproduct",
                        "engine": "faiss",
                    },
                },
                "embed_model": {"type": "keyword"},
            }
        },
    }


def create_index(recreate: bool = False) -> None:
    client = get_client()
    name = config.INDEX_NAME
    if client.indices.exists(index=name):
        if not recreate:
            print(f"인덱스 '{name}' 이미 존재. recreate=True로 재생성 가능.")
            return
        client.indices.delete(index=name)
        print(f"인덱스 '{name}' 삭제.")
    client.indices.create(index=name, body=index_body(config.EMBED_DIM))
    print(f"인덱스 '{name}' 생성 완료 (dim={config.EMBED_DIM}).")
