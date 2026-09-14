"""notices 인덱스 생성 — Nori 한국어 분석기 + kNN 벡터.

한 인덱스에 BM25(Nori)와 벡터를 함께 둔다. 그래서 하이브리드가 이 안에서 된다.
tsvector의 한국어 형태소 문제는 Nori가 근본적으로 해결(조사·어미 분리).

분석기(notices_v2부터):
  korean        색인용  = nori_tokenizer + 품사 필터(notice_pos) + lowercase
  korean_search 검색용  = 위 + 동의어(synonym_graph). 동의어는 검색 시점에만 펼친다(색인 불변).
  - lowercase: 'hbar'처럼 소문자 티커로도 찾히게(기존엔 0건)
  - 품사 필터: 기본은 조사·어미·'하다' 접미사·기호만 지운다(NARROW_STOPTAGS = notices_v3 방식).
    v2는 Nori 기본 stoptags(--pos-stoptags default).
    기본 stoptags는 사전에 없는 코인명 조각을 감탄사·부사·관형사로 보고 지워('아하'토큰, '우'네트워크)
    한글 코인명 검색이 일부 약해졌다. 문장형 질의의 잡음은 대부분 조사·어미라 좁혀도 걸러진다.
  - 사용자 사전은 관리 부담 때문에 쓰지 않는다(사용자 결정).
매핑을 바꾸면 새 인덱스를 만들고 reindex_from()으로 복사한다(재수집 불필요).
"""

from __future__ import annotations

from notice_ai import config
from notice_ai.opensearch_client import get_client

# 검색 시점 동의어(사용자 결정). '입금·출금 = 입출금'은 아직 미결정이라 넣지 않는다.
SYNONYMS = [
    "중지, 중단, 정지",
    "에어드랍, 에어드롭",
    "상장폐지, 거래지원 종료",
]

# 조사(J)·어미(E)·'하다' 접미사(XSV·XSA·VSV)·기호만 지운다. 감탄사(IC)·부사(MAG)·관형사(MM) 등은 남긴다.
NARROW_STOPTAGS = ["E", "J", "XSV", "XSA", "VSV", "SP", "SSC", "SSO", "SC", "SE"]


def index_body(dim: int, pos_stoptags: list[str] | None = NARROW_STOPTAGS) -> dict:
    """품사 필터 기본값은 NARROW_STOPTAGS(v3, 실측 최고). None이면 Nori 기본 stoptags(v2 방식)."""
    pos_filter = {"type": "nori_part_of_speech"}
    if pos_stoptags is not None:
        pos_filter["stoptags"] = list(pos_stoptags)
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
                "filter": {
                    "notice_pos": pos_filter,
                    "notice_synonyms": {"type": "synonym_graph", "synonyms": SYNONYMS},
                },
                "analyzer": {
                    "korean": {"type": "custom", "tokenizer": "nori_user",
                               "filter": ["notice_pos", "lowercase"]},
                    "korean_search": {"type": "custom", "tokenizer": "nori_user",
                                      "filter": ["notice_pos", "lowercase", "notice_synonyms"]},
                },
            },
        },
        "mappings": {
            "properties": {
                "source_url": {"type": "keyword"},   # dedup 키 = 문서 _id로도 사용
                "external_id": {"type": "long"},
                "categories": {"type": "keyword"},    # 배열. term 필터로 포함 검색
                "tickers": {"type": "keyword"},       # 제목에서 추출한 티커(필터용)
                "title": {"type": "text", "analyzer": "korean", "search_analyzer": "korean_search"},
                "raw_text": {"type": "text", "analyzer": "korean", "search_analyzer": "korean_search"},
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


def create_index(recreate: bool = False, pos_stoptags: list[str] | None = NARROW_STOPTAGS) -> None:
    client = get_client()
    name = config.INDEX_NAME
    if client.indices.exists(index=name):
        if not recreate:
            print(f"인덱스 '{name}' 이미 존재. recreate=True로 재생성 가능.")
            return
        client.indices.delete(index=name)
        print(f"인덱스 '{name}' 삭제.")
    client.indices.create(index=name, body=index_body(config.EMBED_DIM, pos_stoptags))
    pos = "기본" if pos_stoptags is None else ",".join(pos_stoptags)
    print(f"인덱스 '{name}' 생성 완료 (dim={config.EMBED_DIM}, 품사 필터 stoptags={pos}).")


def reindex_from(source: str) -> dict:
    """기존 인덱스의 문서를 현재 인덱스(NOTICE_INDEX)로 복사한다. 원본은 건드리지 않는다.

    매핑·분석기를 바꿀 때 재수집(스크래핑) 없이 새 인덱스를 채우는 용도.
    """
    client = get_client()
    dest = config.INDEX_NAME
    if source == dest:
        raise ValueError("원본과 대상 인덱스가 같습니다. NOTICE_INDEX를 새 인덱스 이름으로 설정하세요.")
    if not client.indices.exists(index=dest):
        raise RuntimeError(f"대상 인덱스 '{dest}'가 없습니다. 먼저 setup-index로 만드세요.")
    res = client.reindex(body={"source": {"index": source}, "dest": {"index": dest}},
                         wait_for_completion=True, refresh=True, request_timeout=600)
    return {"source": source, "dest": dest, "total": res.get("total"), "created": res.get("created"),
            "updated": res.get("updated"), "failures": res.get("failures", [])}
