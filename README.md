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
| 코인 목록 — 빗썸 거래 대상 API를 10분마다 받아 이름·티커 자동 채움(메모리 캐시) | ✅ |
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

빗썸 거래 대상 API ──(10분마다)──▶ coins.py 메모리 캐시 ──▶ /coins(자동완성) · 코인 이름·티커 자동 채움
```

```
src/notice_ai/
  api.py               FastAPI (프론트용)          cli.py            명령행(수집·색인·관리용)
  config.py            환경변수                    opensearch_client.py  OpenSearch 연결
  auth.py              공유 토큰·CORS(배포용)
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
  coins.py             빗썸 거래 대상 목록 — 10분마다 갱신하는 메모리 캐시(이름·티커 자동 채움)
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
#       API_TOKEN·ALLOWED_ORIGINS(공개 주소에 올릴 때만, 아래 '배포' 참고)
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
| `GET /coins?q=&limit=&refresh=` | 빗썸 거래 대상 목록(티커·한글명·영문명·마켓·유의 표시). 코인 입력칸 자동완성용 | 없음 |
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

## 코인 목록 (빗썸 거래 대상 API)

코인 한글명·티커를 손으로 적지 않아도 되게, 빗썸 공개 API(`/v1/market/all`)에서 거래 대상 목록을
**10분마다** 받아 둡니다. 저장은 **프로세스 메모리 한 벌**이고 DB를 쓰지 않습니다.

- 티커만 넣으면 한글명을, 한글명만 넣으면 티커를 채웁니다 — `inputs.coins: ["ETH"]` → `이더리움(ETH)`.
- **사람이 적은 값은 덮어쓰지 않습니다.** 비어 있는 쪽만 채웁니다.
- 프론트 자동완성은 `GET /coins?q=이더`로 받습니다(캐시에서 바로 답하므로 빗썸을 매번 부르지 않습니다).
- 빗썸이 죽어 있으면 **갖고 있던 목록을 그대로 씁니다.** 목록을 비우지 않습니다.
- 검색어 별칭(`aliases.expand`)에도 이 이름들이 붙어, 손으로 채운 `data/coin_aliases.json`이
  신규 상장을 몰라도 검색이 걸립니다.

```bash
py -m notice_ai.cli coins            # 전체 목록(캐시가 비면 한 번 받아 온다)
py -m notice_ai.cli coins 이더        # 검색
curl -H "X-API-Token: <토큰>" "https://<백엔드>.onrender.com/coins?q=이더"
```

환경변수(전부 선택, 기본값으로 동작):

| 이름 | 기본값 | 설명 |
|---|---|---|
| `COINS_REFRESH_SEC` | `600` | 갱신 주기(초). `0`이면 자동 갱신을 끕니다 |
| `COINS_TIMEOUT_SEC` | `10` | 빗썸 호출 제한시간(초) |
| `BITHUMB_MARKET_URL` | `https://api.bithumb.com/v1/market/all` | 거래 대상 목록 주소 |

> 메모리 캐시라서 서버가 재시작하면 목록도 사라지고(뜨자마자 다시 받습니다), 인스턴스가
> 여러 개면 각자 갖습니다. 목록이 자주 바뀌지 않아 지금은 이걸로 충분합니다.
> 차후 OpenSearch를 도커로 내리면서 PostgreSQL을 붙일 때 `coins.MemoryCoinStore`만
> 같은 모양의 DB 구현으로 갈아 끼우면 됩니다(조회는 전부 store를 거치게 해 두었습니다).

## 문서
- [docs/PIPELINE.md](docs/PIPELINE.md) — 규칙집. 코드의 기준값을 바꾸면 함께 고칩니다.
- [docs/GOTCHAS.md](docs/GOTCHAS.md) — 겪은 함정과 해결. 막히면 먼저 확인하고, 새로 겪은 것은 여기에 추가합니다.

## 테스트
`tests/`(오프라인 단위 테스트, 검색·초안 평가 스크립트)는 아직 저장소에 포함하지 않았습니다(`.gitignore`).
공유 방법은 [#12](https://github.com/05cherry/notice-draft-ai/issues/12)에서 정합니다. 로컬에서는
`py -m pytest tests -q`로 돌리며, 오프라인 테스트는 OpenSearch·GPT 없이 가짜로 동작합니다.

## 배포 (Render 무료 플랜)

`render.yaml`이 설계도입니다. Render 대시보드에서 **New > Blueprint**로 이 저장소를 고르면 값이 필요한
환경변수를 하나씩 물어보고 그대로 만들어집니다. 프론트(`notice-draft-front`)도 같은 방식으로 정적
사이트로 올립니다.

**만들 때 넣어야 하는 값**

| 환경변수 | 넣을 값 |
|---|---|
| `OPENSEARCH_ENDPOINT` · `OPENSEARCH_USER` · `OPENSEARCH_PASSWORD` | 쓰던 도메인 주소와 아이디·비밀번호 |
| `OPENAI_API_KEY` | 초안 생성을 쓸 때 |
| `AWS_ACCESS_KEY_ID` · `AWS_SECRET_ACCESS_KEY` | Bedrock 임베딩을 쓸 때만. 비워 두면 BM25만 동작 |
| `ALLOWED_ORIGINS` | 프론트 주소. 예) `https://notice-draft-front.onrender.com` |
| `API_TOKEN` | Render가 무작위로 만들어 줍니다. 대시보드에서 확인해 프론트 연결 설정에 넣습니다 |

**접근 통제** — `API_TOKEN`이 있으면 모든 요청에 토큰을 요구합니다(`auth.py`). 토큰 없이 통과하는 것은
`/health`와 CORS 사전 요청뿐입니다. 토큰은 세 가지 방법으로 냅니다.

```bash
curl -H "X-API-Token: <토큰>" https://<백엔드>.onrender.com/types     # 프론트·스크립트
curl -H "Authorization: Bearer <토큰>" ...                            # 같은 뜻
```
브라우저로 `/docs`나 `/ui`를 열 때는 `https://<백엔드>.onrender.com/docs?token=<토큰>`처럼 한 번만 붙이면
쿠키에 담고 주소에서 토큰을 지웁니다. 환경변수를 안 주면 지금까지처럼 아무 검사 없이 돕니다(로컬 개발).

`render.yaml`에 `value:`로 적은 값은 **블루프린트가 계속 강제합니다.** 대시보드에서 고쳐도 다음 동기화 때
되돌아갑니다. 그래서 환경마다 다른 값(계정·자격증명)은 `sync: false`로 두거나, 코드에 기본값이 있으면
아예 적지 않습니다. `LLM_PROVIDER`·`AWS_REGION`·`NOTICE_INDEX`처럼 기본값이 있는 것은 바꿔야 할 때
대시보드에서 직접 더하면 되고, 블루프린트가 되돌려 놓지 않습니다.

**초안 생성이 502로 실패할 때**

`{"detail": "초안 생성 AI 호출 실패 — …", "kind": "auth"}`는 OpenAI가 키를 거부했다는 뜻입니다.
`/health?deep=true`의 `llm`을 먼저 봅니다(토큰 비용 없이 키가 실제로 통하는지 확인합니다).

| `llm` 응답 | 뜻 | 할 일 |
|---|---|---|
| `configured: false` | 키가 아예 없음 | Render 환경변수에 `OPENAI_API_KEY` 추가 |
| `key_ok: false`, 401 `invalid_api_key` | 키가 거부됨 | 값 확인. 따옴표·공백이 섞였거나 폐기된 키. 새로 발급했다면 **재배포**해야 반영됨 |
| `key_ok: true`인데 `/draft`만 403 | 키는 살아 있음 | 그 키에 해당 모델 권한이 없음. OpenAI 프로젝트의 모델 권한과 `OPENAI_MODEL`(기본 `gpt-4o-mini`)·`EVAL_MODEL`(기본 `gpt-4o`) 확인 |
| `key_ok: null` | 확인 못 함 | 네트워크 문제일 수 있음. 서버 로그의 `LLM 실패(...)` 줄을 봅니다 |

키가 맞는지는 이 명령으로도 바로 확인할 수 있습니다(요금 없음).
```bash
curl https://api.openai.com/v1/models -H "Authorization: Bearer <키>"
```

**알아 둘 것**
- 무료 플랜은 15분 동안 요청이 없으면 잠듭니다. 다음 첫 요청이 깨우는 데 1분 가까이 걸립니다.
- `OPENSEARCH_ENDPOINT`는 Render에서 **인터넷으로 닿을 수 있어야** 합니다. VPC 전용이거나 접근 정책이
  특정 IP만 허용하면 붙지 못합니다(무료 플랜은 고정 IP가 없습니다).
- 무료인 것은 서버를 올려 두는 값뿐입니다. OpenSearch 도메인과 GPT 호출 비용은 그대로 나갑니다.
- 공유 토큰은 팀이 나눠 갖는 열쇠 하나지 사용자별 인증이 아닙니다. 프론트에 넣는 순간 그 화면을 여는
  사람은 누구나 토큰을 꺼내 볼 수 있습니다. 사용자별 인증은 [#6](https://github.com/05cherry/notice-draft-ai/issues/6)에서 합니다.

## 비용·보안 주의
- OpenSearch 도메인은 켜 두는 동안 비용이 나갑니다.
- `/draft`는 GPT를 2~4회 부릅니다. 평가(`evaluate`)를 켜면 비용이 약 두 배입니다.
- 공개 주소에 올릴 때는 `API_TOKEN`·`ALLOWED_ORIGINS`를 반드시 줍니다(위 배포 참고). 사용자별 인증은
  아직 없습니다([#6](https://github.com/05cherry/notice-draft-ai/issues/6)).
