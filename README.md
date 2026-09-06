>> 대화는 무조건 한국어로 진행한다. 단, 변수나 클래스명같은건 영어로 계속 표기해준다.

# notice-draft-ai (OpenSearch 버전)

빗썸 공지 작성 자동화. 검색 백엔드를 **OpenSearch(Nori 한국어 형태소 + kNN 벡터)** 로 구성.
PostgreSQL(tsvector/pgvector) 버전을 대체한다.

## 무엇이 바뀌었나 (Postgres 버전 대비)

| 계층 | 이전(Postgres) | 지금(OpenSearch) |
|---|---|---|
| 저장 | `notices` 테이블 | `notices` 인덱스 |
| 한국어 | tsvector `simple`(형태소 X) | **Nori 형태소 분석기** |
| 키워드 검색 | tsvector + trigram | BM25(Nori) |
| 벡터 검색 | pgvector `<=>` | kNN(knn_vector, faiss) |
| 융합 | Python RRF | Python RRF (동일 재사용) |
| 조립/별칭/HyDE/리랭커 | — | **그대로 재사용**(백엔드 무관) |

즉 "두뇌"(assembly, aliases, fusion, hyde, rerank)는 바꿀 게 없었고,
"저장·검색"만 OpenSearch로 교체했다.

## 구성

```
src/notice_ai/
  config.py            환경변수
  opensearch_client.py 연결(FGAC basic auth)
  index_setup.py       Nori + kNN 인덱스 매핑 생성
  collector.py         빗썸 수집 → 색인 (dedup: source_url = _id)
  indexing.py          임베딩 백필(벡터 검색용, 선택)
  search.py            하이브리드(BM25 + kNN + RRF + 리랭커)
  assembly.py          문답 → 검색 입력(필터/쿼리 분리 + 템플릿)
  aliases.py           코인 별칭/티커 추출
  embeddings.py        Bedrock Cohere 임베딩(선택)
  hyde.py              가짜 공지 초안(선택)
  rerank.py            Bedrock 리랭커(선택)
  cli.py               명령행
```

## 환경변수

필수(검색·색인):
```bash
export OPENSEARCH_ENDPOINT="https://search-...-ap-northeast-2.es.amazonaws.com"
export OPENSEARCH_USER="admin"
export OPENSEARCH_PASSWORD="<terraform output -raw master_user_password>"
```

선택(벡터 검색을 켤 때만 — Bedrock 필요):
```bash
export AWS_REGION="ap-northeast-2"
export BEDROCK_EMBED_MODEL="cohere.embed-multilingual-v3"   # 기본값
export BEDROCK_LLM_MODEL="<Bedrock Claude 모델 ID>"          # HyDE 쓸 때만
```

## 실행 순서

```bash
pip install -r requirements.txt

# 1) 인덱스 생성 (Nori + kNN)
python -m notice_ai.cli setup-index

# 2) 공지 수집·색인 (소량 먼저)
python -m notice_ai.cli collect --pages 5

# 3) (선택) 벡터 검색 켜기: 임베딩 백필
python -m notice_ai.cli embed

# 4) 검색
python -m notice_ai.cli search "입출금 일시 중지"                 # BM25만
python -m notice_ai.cli search "입출금 중단" --hybrid --category 입출금   # 하이브리드
python -m notice_ai.cli search "지갑 점검 입금" --hybrid --rerank         # + 리랭커
```

## 단계적 도입 (권장)

벡터·Bedrock 없이 **먼저 BM25(Nori)만으로 시작**해도 된다. `hybrid_search`는
임베딩이 없으면 자동으로 BM25만 쓰도록 되어 있다(graceful degrade).
Nori만으로도 tsvector 대비 한국어 검색이 크게 좋아진다. 벡터는 익숙해진 뒤 켜자.

## 실제 환경에서 확인 필요

- 상세 페이지 본문 셀렉터(`parse_detail_page`)는 article→main→body 폴백.
  실제 상세 페이지 1~2건 색인해보고 헤더/푸터가 섞이면 셀렉터를 좁힐 것.
- 목록 페이지네이션은 `?page=N` 가정. 2페이지가 1페이지와 다른지 확인.
- kNN 필터(효율적 필터링)는 OpenSearch 2.9+ 기준. 엔진 버전 확인.
- Bedrock 임베딩 응답 형식(`embeddings.float`)은 첫 호출 때 실제 응답으로 검증.

## 테스트

```bash
PYTHONPATH=src python -m pytest tests/ -q
```
백엔드 무관 순수 로직(파싱/조립/별칭/RRF)만 테스트한다. OpenSearch/Bedrock 불필요.
