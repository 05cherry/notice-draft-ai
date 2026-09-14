# notice-draft-ai

빗썸 공지 검색·초안 생성 시스템. 임직원이 공지를 쓸 때 ① 비슷한 기존 공지를 찾아 주고, ② 고른 공지의 형식을
본떠 초안을 만든 뒤 사실값을 코드로 검증하며, ③ 맞춤법을 검사합니다(예정).

> **대원칙** — 과거 공지는 형식과 문체의 예시일 뿐 사실 출처가 아닙니다. 새 공지의 코인·날짜·시각·링크·조항은
> 사용자 입력에서만 옵니다. 참고 공지는 사실값을 가린 채 LLM에 넣고, 초안의 사실값은 코드가 입력과 대조합니다.

## 진행 상황

| 기능 | 상태 |
|---|---|
| 공지 검색창 — BM25(Nori) + 1위 점수 50% 컷, 카테고리별 건수, 쪽 나눔, 비슷한 공지(의미 검색) | ✅ |
| 초안 생성 — 입출금·공시·거래유의·안내, 카테고리 1~2개 | ✅ |
| 유형 판별 — 요청문 규칙 + 규칙이 못 정하면 비슷한 공지로 추정(벡터) | ✅ |
| 사실 검증(코드) + GPT 평가 → 기준 미달이면 1회 수정 | ✅ |
| 맞춤법 검사 | ⬜ [#5](https://github.com/05cherry/notice-draft-ai/issues/5) |
| 프론트 | 🔶 별도 폴더 `notice-draft-front`의 React 프로토타입 ([#3](https://github.com/05cherry/notice-draft-ai/issues/3)) |
| 접근 통제(인증·호출 제한) | ⬜ [#6](https://github.com/05cherry/notice-draft-ai/issues/6) |

남은 일은 [GitHub 이슈](https://github.com/05cherry/notice-draft-ai/issues)에 있습니다.

## 구조

```
빗썸 공지 수집(cloudscraper) ─┐
사내 CSV ───────────────────┴─▶ OpenSearch 인덱스 notices_v3 ◀── Bedrock Titan V2 임베딩(1024차원)
                                 (Nori BM25 + kNN)
                                        ▲
FastAPI (api.py) ── /search · /ui ──────┤
   └─ /prepare · /draft · /check ──▶ drafting: 유형 판별 → 후보 검색·참고 공지 선택 → 프롬프트
                                        → LLM(openai / local / company) → factcheck(코드 검증) + evaluator(GPT 평가)
```

```
src/notice_ai/
  api.py               FastAPI (프론트용)          cli.py            명령행(수집·색인·관리용)
  config.py            환경변수                    opensearch_client.py  OpenSearch 연결
  scraper_client.py    cloudscraper + _next/data   collector.py      공지 수집 → 색인
  ingest.py            사내 CSV 색인               index_setup.py    인덱스 생성(Nori + kNN)
  embeddings.py        Bedrock 임베딩(Titan V2)    indexing.py       임베딩 백필
  search.py            BM25·kNN 검색, 검색창(search_page)
  notice_types.py      유형 스펙(카테고리 > 유형·필드·섹션·규칙) + 라우팅 + 입력 정규화·검증
  factcheck.py         참고 공지 사실값 가리기 + 초안 사실 검증
  drafting.py          초안 파이프라인(유형 추정·후보·선택·프롬프트·생성·수정)
  evaluator.py         GPT 평가(5개 기준, 수정 여부 판정)
  llm.py               LLM 인터페이스(openai / local / company), 제한시간·오류 종류
  health.py            /health?deep=true 상태 점검
  aliases.py           코인 별칭·티커 추출(수집·CSV 색인 때)
  fusion.py hyde.py rerank.py   RRF 융합·가상 공지·리랭커 — CLI `search --hybrid`에서만 씀
  assembly.py          옛 문답 → 검색어 조립. 지금 흐름에서는 쓰지 않음(#12)
  prompts/             common.txt + category/<카테고리>.txt + subtype/<카테고리>/<유형>.txt
  web/search.html      간단한 검색 화면(/ui)
data/                  categories.json(카테고리 ID) · coin_aliases.json(코인 별칭)
docs/                  PIPELINE.md(규칙집) · GOTCHAS.md(겪은 함정)
```

## 빠른 시작

**1. 설치** (Python 3.13)
```bash
pip install -r requirements.txt
```

**2. 환경변수** — 프로젝트 폴더의 `.env`에 둡니다(`.gitignore`로 제외됨, 커밋 금지).
```bash
OPENSEARCH_ENDPOINT=https://search-...-ap-northeast-2.es.amazonaws.com
OPENSEARCH_USER=admin
OPENSEARCH_PASSWORD=...
OPENAI_API_KEY=...
LLM_PROVIDER=openai
# 선택: NOTICE_INDEX(기본 notices_v3) · OPENAI_MODEL(gpt-4o-mini) · EVAL_MODEL(gpt-4o)
#       LLM_TIMEOUT(90초) · LLM_MAX_RETRIES(1) · AWS_REGION(ap-northeast-2) · BEDROCK_EMBED_MODEL
```
Bedrock 임베딩은 `~/.aws/credentials`(`aws configure`)의 자격증명을 씁니다. 없어도 검색·초안은 BM25만으로
동작하고, 유형 추정·비슷한 공지·하이브리드만 빠집니다.

**3. 인덱스와 데이터** (CLI는 `PYTHONPATH=src`가 필요합니다)
```bash
py -m notice_ai.cli setup-index                    # Nori + kNN 인덱스 생성
py -m notice_ai.cli collect                        # 공지 수집·색인 → 새 공지 임베딩까지 (--no-embed로 생략)
py -m notice_ai.cli ingest-csv 공지데이터.csv       # 사내 CSV 색인(전체 이력·본문) → 새 공지 임베딩까지
py -m notice_ai.cli embed                          # 임베딩 없는 공지만 채우기(재실행 안전)
```
`feed.bithumb.com`은 Cloudflare와 429에 민감합니다. 대량 수집은 천천히 하고, 운영에서는 CSV 경로를 우선합니다.

**4. 서버**
```bash
py -m uvicorn notice_ai.api:app --app-dir src --env-file .env --reload --port 8000
```
- http://localhost:8000/docs — API 문서(눌러 보며 호출)
- http://localhost:8000/ui — 검색 화면
- http://localhost:8000/health?deep=true — 검색 서버·임베딩·LLM 설정 점검

## API

| 엔드포인트 | 하는 일 | LLM |
|---|---|---|
| `GET /search?q=&category=&page=&size=&sort=` | 공지 검색창. 1위 점수 50% 미만 제외, `total`·카테고리별 건수·`related`(비슷한 공지) | 없음 |
| `GET /ui` | 간단한 검색 화면 | 없음 |
| `GET /types` | 카테고리별 유형과 필수·선택 입력(질문 문구 포함) | 없음 |
| `POST /prepare` | 유형 판별(추정 포함) + 빠진 입력 질문 + 참고 공지 후보 5건·자동 선택 이유 | 없음 |
| `POST /draft` | 초안 생성 → 코드 검증 + GPT 평가 → 필요 시 1회 수정 → 최종 초안 | 2~4회 |
| `POST /check` | 사용자가 고친 초안을 코드로만 다시 검사 | 없음 |
| `GET /notice?url=` | 공지 1건(원문 + 초안이 참고하는 최초 버전 + 판별 유형) | 없음 |
| `GET /health` | 생존 확인. `?deep=true`면 의존 서비스 상태 | 없음 |

- 요청(/prepare·/draft·/check 공통): `{categories:[1~2개], text, inputs, subtypes?, part_inputs?, base_notice_url?, evaluate?, hybrid?}`
- 문답은 무상태입니다. 프론트가 매번 전체 값을 보내고, 서버는 `missing_fields`로 다음 질문을 알려 줍니다.
- 외부 서비스가 실패하면 `{"detail": 안내 문구, "kind"}`로 답합니다(GPT 502/504, 검색 서버 503 등).

단계별 규칙, 기준값, 유형 표, 참고 공지 선택, 검증 항목, 오류 응답, 검색창 규칙은
**[docs/PIPELINE.md](docs/PIPELINE.md)** 에 있습니다.

## 문서
- [docs/PIPELINE.md](docs/PIPELINE.md) — 규칙집. 코드의 기준값을 바꾸면 함께 고칩니다.
- [docs/GOTCHAS.md](docs/GOTCHAS.md) — 겪은 함정과 해결. 막히면 먼저 확인하고, 새로 겪은 것은 여기에 추가합니다.

## 테스트
`tests/`(오프라인 단위 테스트, 검색·초안 평가 스크립트)는 아직 저장소에 포함하지 않았습니다(`.gitignore`).
공유 방법은 [#12](https://github.com/05cherry/notice-draft-ai/issues/12)에서 정합니다. 로컬에서는
`py -m pytest tests -q`로 돌리며, 오프라인 테스트는 OpenSearch·GPT 없이 가짜로 동작합니다.

## 비용·보안 주의
- OpenSearch 도메인은 켜 두는 동안 비용이 나갑니다.
- `/draft`는 GPT를 2~4회 부릅니다. 평가(`evaluate`)를 켜면 비용이 약 두 배입니다.
- 지금은 인증이 없고 CORS를 전부 허용합니다. 사내망에서만 쓰고, 운영 전에 접근 통제를 넣습니다([#6](https://github.com/05cherry/notice-draft-ai/issues/6)).
