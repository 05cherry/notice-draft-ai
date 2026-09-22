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
  - 사용자 사전(user_dictionary_rules): 코인 한글명을 한 낱말로 붙들어 둔다.
    사전에 없는 이름은 조사·어미처럼 생긴 조각으로 잘려 검색이 안 된다('네이로'→'네이'+'로').
    예전에는 목록을 손으로 관리해야 해서 쓰지 않았는데, coins.py가 빗썸에서 거래 대상을
    받아 오면서 그 부담이 없어졌다(#34).
    파일(user_dictionary)이 아니라 설정에 직접 넣는 rules 방식이다 — AWS 관리형에서는
    노드에 파일을 올릴 수 없다(커스텀 패키지 업로드가 따로 필요).
매핑을 바꾸면 새 인덱스를 만들고 reindex_from()으로 복사한다(재수집 불필요).
"""

from __future__ import annotations

import logging
import re

from notice_ai import aliases, coins, config
from notice_ai.opensearch_client import get_client

logger = logging.getLogger(__name__)

# 검색 시점 동의어(사용자 결정). '입금·출금 = 입출금'은 아직 미결정이라 넣지 않는다.
SYNONYMS = [
    "중지, 중단, 정지",
    "에어드랍, 에어드롭",
    "상장폐지, 거래지원 종료",
]

# 조사(J)·어미(E)·'하다' 접미사(XSV·XSA·VSV)·기호만 지운다. 감탄사(IC)·부사(MAG)·관형사(MM) 등은 남긴다.
NARROW_STOPTAGS = ["E", "J", "XSV", "XSA", "VSV", "SP", "SSC", "SSO", "SC", "SE"]

_HANGUL_RE = re.compile(r"[가-힣]")
MIN_DICT_WORD = 2      # 한 글자는 넣지 않는다. 흔한 글자를 명사로 붙들면 엉뚱한 곳이 잘린다.
MAX_DICT_WORDS = 3000  # 설정에 직접 넣는 값이라 무한정 늘릴 수 없다. 넘으면 자르고 경고한다.


def user_dictionary_rules(extra: list[str] | None = None) -> list[str]:
    """Nori 사용자 사전 규칙 — 코인 한글명을 한 낱말로 붙들어 둔다.

    출처는 둘이다.
      - coins.known(): 빗썸 거래 대상(자동 갱신). 신규 상장도 따라온다.
      - data/coin_aliases.json: 손으로 채운 별칭(옛 이름·표기 흔들림).
    호출하기 전에 coins.refresh()로 목록을 채워 두면 더 많이 잡힌다. 비어 있어도 별칭만으로 돈다.

    한글이 든 이름만 넣는다. 영문명은 Nori가 형태소로 쪼개지 않으므로 사전이 필요 없고,
    괜히 넣으면 규칙만 불어난다. 정렬해서 돌려주므로 같은 목록이면 같은 설정이 나온다
    (설정이 바뀌면 인덱스를 다시 만들어야 하므로, 순서 때문에 달라 보이면 안 된다).
    """
    words: set[str] = set(extra or ())
    for coin in coins.known().values():
        words.add(coin.name)
        words.update(aliases.expand(coin.ticker))
    for names in aliases._seed().values():
        words.update(names)

    out = sorted({
        w for raw in words
        if (w := str(raw or "").strip())
        and len(w) >= MIN_DICT_WORD
        and _HANGUL_RE.search(w)          # 한글이 든 것만 — 영문명은 쪼개지지 않는다
        and " " not in w                  # 규칙에서 공백은 복합명사 분해 문법이라 이름에 쓸 수 없다
    })
    if len(out) > MAX_DICT_WORDS:
        logger.warning("사용자 사전 %d개가 상한(%d)을 넘어 잘라 냅니다.", len(out), MAX_DICT_WORDS)
        out = out[:MAX_DICT_WORDS]
    return out


def index_body(dim: int, pos_stoptags: list[str] | None = NARROW_STOPTAGS,
               dict_rules: list[str] | None = None) -> dict:
    """품사 필터 기본값은 NARROW_STOPTAGS(v3, 실측 최고). None이면 Nori 기본 stoptags(v2 방식).

    dict_rules 를 주면 그대로 사용자 사전으로 쓴다. 안 주면 지금 아는 코인명으로 만든다.
    빈 목록([])을 주면 사전 없이 만든다(사전 넣기 전과 비교할 때).
    """
    pos_filter = {"type": "nori_part_of_speech"}
    if pos_stoptags is not None:
        pos_filter["stoptags"] = list(pos_stoptags)
    rules = user_dictionary_rules() if dict_rules is None else dict_rules
    tokenizer = {
        "type": "nori_tokenizer",
        "decompound_mode": "mixed",  # 복합명사 원형+분해 모두 색인
    }
    if rules:
        tokenizer["user_dictionary_rules"] = rules
    return {
        "settings": {
            "index": {"knn": True},
            "analysis": {
                "tokenizer": {"nori_user": tokenizer},
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


def _tokens(client, *, text: str, index: str = "", analyzer: str = "",
            rules: list[str] | None = None, pos_stoptags=NARROW_STOPTAGS) -> list[str]:
    """한 문자열이 어떤 토큰으로 쪼개지는지. 인덱스 없이도 분석기를 즉석에서 만들어 볼 수 있다."""
    if index:
        body = {"analyzer": analyzer or "korean", "text": text}
        res = client.indices.analyze(index=index, body=body)
    else:
        tokenizer = {"type": "nori_tokenizer", "decompound_mode": "mixed"}
        if rules:
            tokenizer["user_dictionary_rules"] = rules
        pos = {"type": "nori_part_of_speech"}
        if pos_stoptags is not None:
            pos["stoptags"] = list(pos_stoptags)
        res = client.indices.analyze(body={"tokenizer": tokenizer, "filter": [pos, "lowercase"], "text": text})
    return [t["token"] for t in res.get("tokens", [])]


def diagnose_dictionary(limit: int = 0, *, refresh: bool = True) -> dict:
    """사용자 사전이 실제로 필요한지, 넣으면 나아지는지 센다 (#34).

    코인 한글명 하나하나를 지금 인덱스의 분석기와 사전을 넣은 분석기로 각각 쪼개 보고 견준다.
      쪼개짐  이름이 토큰 하나로 안 남는다 = 그 이름으로 검색하면 안 걸린다
      고쳐짐  사전을 넣으니 토큰 하나가 됐다
      남음    사전을 넣어도 여전히 쪼개진다(이름에 공백이 있거나 다른 이유)

    인덱스를 만들지 않고 _analyze 로만 보므로 지금 색인에는 아무 영향이 없다.
    """
    client = get_client()
    if refresh:
        coins.refresh()
    rules = user_dictionary_rules()
    names = sorted({c.name for c in coins.known().values() if c.name and _HANGUL_RE.search(c.name)})
    if not names:
        return {"error": "코인 목록이 비어 있습니다. 빗썸 호출이 됐는지 확인하세요.", "rules": len(rules)}
    if limit:
        names = names[:limit]

    split, fixed, still = [], [], []
    for name in names:
        now = _tokens(client, text=name, index=config.INDEX_NAME)
        if now == [name.lower()]:
            continue                      # 지금도 멀쩡하다
        split.append(name)
        after = _tokens(client, text=name, rules=rules)
        (fixed if after == [name.lower()] else still).append((name, now, after))
    return {
        "rules": len(rules), "checked": len(names),
        "split": len(split), "fixed": len(fixed), "still": len(still),
        "fixed_examples": [(n, now) for n, now, _ in fixed[:15]],
        "still_examples": [(n, now, after) for n, now, after in still[:15]],
    }


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
