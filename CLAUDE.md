# notice-draft-ai

빗썸 공지사항 검색 · 초안 생성 시스템. 임직원이 공지를 쓸 때 (1) 비슷한 기존 공지를
찾아주고, (2) 고른 공지를 본떠 초안을 생성하며, (3) 맞춤법을 검사한다(예정).

## 핵심 요구사항
1. 유사 공지 검색 — 기존 공지를 DB에 넣고 비슷한 걸 찾아준다. ✅
2. 초안 생성 — 대카테고리 선택 → 문답으로 내용 추림 → 유사 공지 중 하나를 골라,
   그 형식을 유지하며 핵심 내용만 바꿔 LLM이 초안 생성. ✅
3. 맞춤법 검사. ⬜ (미구현)

## 아키텍처
- **저장·검색**: AWS OpenSearch (Nori 한국어 형태소 + kNN 벡터). Terraform으로 서울 리전에 프로비저닝.
- **수집**: 공식 공지 API는 최신 20건·본문 없음이라, cloudscraper + Next.js `_next/data`
  엔드포인트로 목록·본문·전체 이력을 수집. 전체 이력·본문은 사내 CSV(`ingest`)로도 채운다.
- **검색**: BM25(Nori) + 벡터(kNN) 하이브리드 → RRF 융합 → (선택) 리랭커. 벡터/임베딩은
  없어도 BM25만으로 graceful degrade.
- **초안 생성**: 기준 공지 + 최신 유사 공지 + 문답값 → LLM. 고위험 값(코인·티커·날짜·법령)은
  코드로 사후 검증. LLM은 인터페이스로 추상화(openai/local/company를 환경변수로 전환).
- **진입점**: FastAPI(`api.py`, 프론트용) + CLI(`cli.py`, 수집·관리용).

## 소스트리
```
src/notice_ai/
  config.py            환경변수
  opensearch_client.py OpenSearch 연결
  scraper_client.py    cloudscraper + _next/data (Cloudflare 우회)
  collector.py         공지 수집 → 색인
  ingest.py            사내 CSV 색인
  index_setup.py       인덱스 생성 (Nori + 벡터)
  indexing.py          임베딩 백필 (선택)
  search.py            하이브리드 검색 (BM25 + 벡터 + RRF)
  assembly.py          문답 → 검색어 조립 (필터/쿼리 분리, 템플릿)
  aliases.py           코인 별칭 확장
  fusion.py            RRF
  embeddings.py hyde.py rerank.py   벡터 관련 (선택)
  llm.py               LLM 인터페이스 (openai/local/company)
  drafting.py          초안 생성 + 고위험값·법령 검증
  evaluator.py         평가 agent (초안 채점)
  prompts/
    common.txt         공통 지침
    category/{입출금,거래유의}.txt   카테고리별 지침 (있으면 공통에 얹음)
  api.py               FastAPI (/search, /draft)
  cli.py               명령행
tests/
  test_pipeline.py     순수 로직 단위테스트
  run_drafts.py        카테고리별 초안 자동 테스트 + 평가 agent
data/
  categories.json      카테고리 ID 맵
  coin_aliases.json    코인 별칭
```

## 실행
```bash
# 환경변수 (매 세션 필요; env.sh로 묶어 source 권장, gitignore 필수)
export PYTHONPATH=src
export OPENSEARCH_ENDPOINT='https://search-...-ap-northeast-2.es.amazonaws.com'
export OPENSEARCH_USER='admin'
export OPENSEARCH_PASSWORD='...'
export OPENAI_API_KEY='sk-...'
export LLM_PROVIDER='openai'          # 또는 local / company

# 인덱스 생성 (Nori 플러그인 연결 + space_type=innerproduct 확인)
py -m notice_ai.cli setup-index

# 수집 (스크래핑; 429 민감 → 천천히. 카테고리별/전체)
py -m notice_ai.cli collect --category 입출금 --max-pages 1
py -m notice_ai.cli collect

# 사내 CSV 색인 (전체 이력·본문)
py -m notice_ai.cli ingest-csv 공지데이터.csv

# 검색
py -m notice_ai.cli search "입출금 중단" --category 입출금

# 웹 서버
py -m uvicorn notice_ai.api:app --reload --port 8000   # http://localhost:8000/docs

# 초안 자동 테스트 + 평가 (카테고리별, GPT 비용 발생)
py tests/run_drafts.py                 # 전체
py tests/run_drafts.py 입출금 거래유의   # 특정
py tests/run_drafts.py --no-eval       # 평가 생략(빠름/저렴)

# 단위 테스트
py -m pytest tests/test_pipeline.py -q
```

## API
- `GET  /search?q=&category=&hybrid=&rerank=&limit=` — 유사 공지 검색
- `POST /draft` — `{base_notice_url, answers:{coin_kr,ticker,action,reason,datetime}, category}` → 초안 + warnings

## 카테고리 (이름:ID)
거래유의:5, 거래지원종료:6, 공시:15, 마켓 추가:9, 수수료 이벤트:16, 신규서비스:2,
안내:1, 업데이트:4, 이벤트:8, 입출금:7, 점검:3, 후기:17

## 주의사항 / 규칙
- **고위험 값**(코인명·티커·날짜·법령 조항)은 LLM이 지어내지 않게 문답값을 그대로 주입하고
  생성 후 검증. 법령 조항이 들어가면 항상 "검증 필요" 경고를 띄운다(사유마다 조항이 다름).
- **최신 공지 우선**: 인사말·끝인사·어투·유의사항은 최신 유사 공지 표현을 우선.
- **카테고리별 프롬프트**: `prompts/category/<카테고리>.txt`가 있으면 공통에 얹힌다.
  새 카테고리 지침은 파일만 추가하면 됨(코드 수정 불필요).
- **스크래핑**: `feed.bithumb.com`은 Cloudflare + 429에 민감. 대량 수집은 천천히, 429가
  반복되면 중단. 운영에서는 사내 정식 데이터 경로(CSV `ingest`)를 우선.
- **인덱스 매핑 주의**: `space_type`은 faiss에서 `innerproduct`(cosinesimil 아님).
  `published_at` 포맷은 `yyyy-MM-dd HH:mm:ss` 포함. 매핑 변경 시 `setup-index --recreate` 후 재수집.
- **비용**: OpenSearch는 켜두면 과금. 안 쓰면 `terraform destroy`(데이터 삭제됨). 평가 agent는
  GPT를 추가 호출하므로 비용 두 배.

## 진행 상황
- ✅ 1. 검색 API  ✅ 2. 초안 생성 + 평가 agent + 카테고리별 프롬프트
- ⬜ 3. 맞춤법 검사   ⬜ 4. 문답 흐름 설계   ⬜ 5. React 프론트

## 다음 단계 메모
- 3번(맞춤법): `/spellcheck` 엔드포인트. 한국어 맞춤법 검사기 선택 필요.
- 4번(문답 흐름): 프론트와 여러 번 오가는 상태 관리 설계 필요(서버 세션 vs 프론트가 매번 전송).
- 인증은 현재 없음. 운영 전 사내망/IP 제한 등 접근 통제 추가 예정.