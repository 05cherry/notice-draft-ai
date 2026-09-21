# GOTCHAS — 겪은 함정과 해결

작업하다 막혔던 것과 해결법을 한 줄씩 쌓는 곳. 같은 실수를 반복하지 않기 위한 로그.
새 이슈를 만나면 맨 아래에 추가한다. (형식: 증상 → 원인 → 해결)

## OpenSearch / 인덱스
- **kNN 인덱스 생성 400 "does not support space type cosinesimil"**
  → faiss 엔진은 cosinesimil 미지원. `space_type: innerproduct` 사용
  (Cohere 등 정규화된 임베딩이면 코사인과 동일 결과).
- **색인 400 "failed to parse field [published_at]"**
  → 매핑의 date format이 값과 불일치. format에 `yyyy-MM-dd HH:mm:ss` 포함해야 함.
- **매핑을 바꿨는데 반영 안 됨**
  → 인덱스는 한 번 만들면 필드 매핑이 고정. `setup-index --recreate`로 재생성 후 재수집.
- **`Unknown tokenizer type [nori_tokenizer]`**
  → AWS 관리형 OpenSearch는 Nori가 기본 내장 아님. 콘솔에서 `analysis-nori` 패키지를
  도메인에 **연결(Associate)** 해야 함. 상태가 Available 될 때까지 대기.
- **동의어(search_analyzer)를 넣었는데 검색에 반영 안 됨**
  → 쿼리(multi_match)에 `"analyzer": "korean"`을 직접 지정하면 필드의 search_analyzer가 무시된다.
  쿼리에서 analyzer 지정을 빼면 새 인덱스는 korean_search, 옛 인덱스는 korean이 쓰인다.
  synonym_graph는 검색 시점(search_analyzer)에만 둔다(색인 불변, 동의어 바꿔도 재색인 불필요).
- **nori_part_of_speech를 켜면 사전에 없는 한글 코인명 검색이 일부 약해짐**
  → 조각난 이름의 일부를 조사·어미·감탄사로 보고 지운다('헤데라'→'데', '메가이더'→'메').
  실측(실제 검색, 코인명 873개): 평균 0.899→0.886, 크게 하락 25개(아하토큰·폰케 100%→0% 등).
  대신 소문자 티커(0→100%), 동의어(상장폐지 0→100%), 띄어쓰기·문장형 질의는 크게 좋아짐.
  품사 필터 없는 보조 필드를 섞는 방식은 RRF 시뮬레이션에서 동의어·문장형 이득을 크게 잃어 기각.
  초안 파이프라인은 검색어에서 코인명을 빼므로 영향 없음(12개 시나리오 중 11개 동일 선택).
  → (해결) 지워지던 코인명 조각은 주로 감탄사(IC)·부사(MAG)·관형사(MM)로 오판된 것이고, 문장 잡음은
  대부분 조사(J)·어미(E). stoptags를 조사·어미·하다접미사·기호로 좁힌 notices_v3: 코인명 0.903(최고),
  문장형·동의어·띄어쓰기는 v2와 동일. 품사 필터를 통째로 빼면 코인명은 돌아오지만 문장형 92%→52%,
  띄어쓰기 100%→60%로 떨어진다(추정). 어미로 분석되는 조각('네이'로, 알'로라')은 사전 없이는 못 살림.
- **d0의 제목만 쓴 시뮬레이션과 실제 검색 결과가 다름**
  → 코인명 평균이 시뮬레이션에선 개선(0.652→0.678), 실제(title^3+raw_text)에선 소폭 하락. 분석기 변경은
  반드시 실제 bm25_search로 전후 비교한다.
- **인덱스 Health가 yellow**
  → 단일 노드라 복제본을 둘 곳이 없어서. 정상. 무시해도 됨.

## 접속 / 인증
- **401 AuthenticationException**
  → FGAC 마스터 비번 불일치. `terraform output -raw master_user_password`로 정확히 확인.
  브라우저로 `/_dashboards/` 로그인해서 비번 자체가 맞는지 먼저 검증.
- **도메인이 콘솔에 안 보임**
  → 리전이 다른 것. 오른쪽 위에서 서울(ap-northeast-2)로 전환.
- **Discover에 데이터가 안 보임**
  → 시간 필터 때문. 오른쪽 위 시간 범위를 "Last 1 year" 등으로 넓히기.

## 수집 / 스크래핑
- **feed.bithumb.com 403 (Cloudflare)**
  → 일반 요청은 Cloudflare에 막힘. cloudscraper 사용. 공식 공지 API(api.bithumb.com)는
  Cloudflare 없지만 최신 20건·본문 미제공.
- **목록 JSON 404**
  → `_next/data/{buildId}/notice.json`은 `category` 파라미터 필수(없으면 404).
  category=0은 빈 결과. 실제 카테고리 ID로 조회(data/categories.json).
- **buildId가 매번 바뀜**
  → 사이트 재배포 시 변경. 매 실행마다 /notice HTML에서 새로 추출.
- **429 Too Many Requests**
  → 요청 속도 민감. 재시도로 밀어붙이면 차단이 더 길어짐. 멈추고 5~10분(길면 하룻밤) 대기.
  대량 수집은 간격을 넉넉히(scraper gap ↑). 운영은 사내 CSV 경로 우선.

## 환경 / 실행 (Windows)
- **ModuleNotFoundError: No module named 'notice_ai'**
  → `PYTHONPATH=src` 미설정. 코드가 src/ 아래 있음.
- **터미널마다 환경변수가 사라짐**
  → 창을 새로 열면 export가 초기화됨. env.sh로 묶어 `source env.sh`(gitignore 필수).
  서버(uvicorn) 창은 환경변수 6개가 모두 있어야 함.
- **Git Bash에서 비번 `!` → "event not found"**
  → 히스토리 확장 때문. 비번을 작은따옴표(')로 감싸기. cmd로 통일하면 회피.
- **`pip: command not found` / `python not found`**
  → `py -m pip ...`, `py ...` 사용(Windows 파이썬 런처).
- **`UnicodeEncodeError: 'cp949' codec can't encode character`**
  → Windows 콘솔 기본 인코딩이 cp949라 한글·특수문자(—, ✅ 등) 출력에서 죽음.
  `PYTHONIOENCODING=utf-8`을 붙여 실행(예: `PYTHONIOENCODING=utf-8 py tests/run_drafts.py`).
- **`ModuleNotFoundError: No module named 'openai'`**
  → `LLM_PROVIDER=openai`면 openai 패키지 필요. `py -m pip install -r requirements.txt`.
- **`.env`를 만들어 뒀는데 환경변수가 안 먹음**
  → 코드가 python-dotenv를 쓰지 않아 자동 로드되지 않음. 셸에 직접 주입해야 함
  (Git Bash: `set -a; . ./.env; set +a`).
- **Terraform "Unsupported block type dedicated_master"**
  → dynamic 블록 오작성. dedicated_master_enabled/type/count 단순 속성으로.

## Bedrock / 임베딩
- **`cohere.embed-multilingual-v3` 호출 시 ValidationException(invalid model identifier)**
  → 서울 리전(ap-northeast-2)에 없는 모델. 서울 온디맨드 임베딩은 `amazon.titan-embed-text-v2:0`(기본값으로 변경).
  `cohere.embed-v4:0`은 전역 교차 리전 프로파일(`global.cohere.embed-v4:0`)로만 되고 Marketplace 약관 동의 필요.
- **권한 조회(get_foundation_model_availability)는 AUTHORIZED인데 InvokeModel이 AccessDeniedException**
  → 메시지가 "Your account is currently being verified"면 AWS 계정 인증이 진행 중인 것(보통 2시간 이내).
  코드·IAM 문제가 아니다. 기다렸다가 다시 시도하고, 2시간이 넘으면 AWS에 문의(aws-verification@amazon.com).
- **이 PC의 AWS 자격증명이 루트 계정 키**
  → 운영 전 최소 권한 IAM 사용자/역할로 교체 권장(Bedrock InvokeModel 등 필요한 것만).
  (2026-09-15: 05mango IAM 사용자 키로 교체. 운영 전 최소 권한 분리는 여전히 필요)
- **`aws sts get-caller-identity`가 SignatureDoesNotMatch**
  → 키 ID는 맞는데 시크릿이 틀린 것(환경변수가 없으면 ~/.aws/credentials 값). 손으로 옮겨 적으면
  l/I/1, O/0, 대소문자에서 틀리기 쉽다(실제로 40자 중 2자 오타). CSV에서 복사해 `aws configure`에 붙여넣기.
- **벡터 검색 결과마다 1024차원 벡터가 딸려 옴**
  → knn 쿼리에 `_source.excludes: ["embedding"]`가 없었다(BM25 쪽에만 있었음). 추가함.
- **Titan 코사인 유사도는 0.3~0.6에 몰려 있어 BM25(최댓값 대비 0~1)와 그대로 섞으면 차이가 안 난다**
  → 하이브리드 순위에서는 후보 안 최소~최대로 0~1로 펴서 섞는다(drafting._relevance).
- **초안 후보 검색에 하이브리드를 켜도 문장형 요청이 여전히 엉뚱한 공지를 고름**
  → 검색이 아니라 유형 판별 문제. 규칙(route)이 '늦게 처리', '기간을 늘리려고' 같은 말투를 못 잡아 general로
  가면 등급(tier)이 general 기준으로 매겨진다. 정답 유형을 주면 BM25만으로도 96%. (tests/eval_hybrid.py [1b])
  → 규칙이 general이면 뜻이 가까운 공지 5건의 유형 다수결로 추정(drafting.resolve_with_estimate). 2/11 → 10/11.
  추정은 틀릴 수 있어(예: '명절 상담 시간 변경' → 서비스 장애) 응답에 estimated로 표시하고 사용자가 바꾸게 한다.
- **오프라인 테스트가 Bedrock을 부르려 함**
  → 유형 추정은 draft_notice/check_notice 안에서 자동으로 돈다. tests/conftest.py의 autouse fixture가
  drafting.semantic_neighbors를 빈 결과로 바꿔 둔다(모듈 속성을 호출 시점에 찾으므로 monkeypatch가 먹는다).

## LLM / 초안
- **openai RateLimitError: insufficient_quota**
  → API 크레딧 0. ChatGPT 구독과 API 크레딧은 별개. platform.openai.com에서 충전.
- **날짜 검증 오탐**
  → LLM이 2026-09-01을 2026.09.01(금)로 바꾸면 문자열 불일치. 숫자 시퀀스로 관대 비교.
- **법령 조항을 LLM이 참고 공지에서 그대로 복사**
  → 사유마다 조항이 다름. 조항이 들어가면 항상 "검증 필요" 경고(C 방향). 프롬프트로도 경고.
  → (추가) 참고 공지의 조항 번호를 프롬프트에서 `[조항 확인 필요]`로 가림(결정 a). 입력에 없는 조항이
  초안에 있으면 factcheck가 error → 1회 수정.
- **제목에 중지 날짜를 재개 날짜로 씀 '(09/01 재개)'**
  → 참고 공지 제목 '(06/05 재개)' 꼴을 따라 한 것. resume_at 미입력이면 프롬프트에 "재개 표기 금지"를
  명시하고, 제목의 '(MM/DD 재개)'를 factcheck가 error로 잡는다.
- **참고 공지의 상태 서술 복사 '현재 OO의 입금이 중단된 상태입니다'**
  → 거래유의 지정 공지마다 다른 사실. `deposit_status` 입력이 없으면 guard 규칙으로 error.
- **LLM이 [참고한 공지 유형]에 참고 공지 제목(옛 코인·날짜)을 그대로 씀**
  → 출력 형식에서 그 섹션을 뺐다. 참고 공지 정보는 코드가 `selected_reference`로 구조화해 반환.
- **수정 요청을 보냈는데 LLM이 지적된 문장을 안 고침**(에어드랍 '가스(GAS) 보유자 대상')
  → 문제 메시지가 "보유 기준 가상자산 자리에 다른 가상자산: '(GAS) 보유자'"뿐이라, 다른 줄의 맞는 값을 보고
  해결됐다고 여김. 틀린 문장 전체 + 정답 + 고칠 표현을 적고 "인용된 문장 자체를 고칠 것"을 지시하니
  같은 불량 초안 기준 수정 성공 0/5 → 5/5. 코드 검증 메시지는 LLM이 바로 행동할 수 있게 쓴다.
- **에어드랍 1차 초안에서 지급 코인·보유 코인이 뒤바뀜**(심하면 통째로 반대)
  → ① 참고 공지 제목과 본문을 따로 가려 번호 체계가 달랐음(제목 '<가상자산명>'=GAS, 본문 '<가상자산명1>'=NEO)
  → 한 덩어리로 가려 통일. ② 입력값 이름 '대상 가상자산'이 공지 속 '지급 대상: … 보유 회원'과 혼동 → 유형별 이름
  (지급 가상자산 / 보유 기준 가상자산). ③ 가려진 자리가 어떤 입력값인지 코드가 대응표로 제공.
  1차 초안 역할 오류: 최근 3/3 → 0/5.
- **1차 초안이 입력한 지급일을 빠뜨림** → (해결) 누락이 아니라 유형 설계 문제였다.
  에어드랍은 '지급 완료 안내'(159건, 138건이 날짜 없음)와 '지원·지급 예정 안내'(114건, 77건이 일시 있음)로 나뉜다.
  완료형에 지급일을 요구해 매번 오류·수정이 돌았음 → airdrop_paid(지급일 안 받음)/airdrop_plan(지급 예정일·출금 오픈)으로
  분리. 제목만으론 안 갈려('…4회차 지급 안내'인데 본문은 예정) 과거 공지는 본문 표현으로 분류(family/body_pattern).
  결과: 완료형·예정형 모두 1차 초안부터 오류 0건(각 3/3). (프롬프트로 지급일을 강조하는 방식은 0/6로 효과 없었음)
- **에어드랍 지원 안내가 지급 뒤 '(11/07 지급)'으로 제목이 바뀜** → 날짜+지급/출금 끝 괄호도 업데이트 꼬리표로 제거.
- **역할 규칙이 줄을 넘어 '…10:00(KST)⏎지급 수량'을 한 덩어리로 읽어 KST를 코인으로 봄** → 정규식 공백을 `[ \t]*`로,
  비코인 약어(KST 등)는 역할 검사 제외.
- **평가 LLM이 점수를 "4"(문자열)로 줌 → 합계 과소 → 불필요한 수정**
  → evaluator가 int로 변환. 평가 실패(JSON 깨짐)면 평가를 근거로 수정하지 않는다.
- **`/draft`가 502 "초안 생성 AI 호출 실패 — LLM API 키가 틀렸거나 권한이 없습니다"**
  → 401(키 거부)과 403(키는 살아 있으나 그 모델·프로젝트 권한 없음)을 한 문장으로 뭉뚱그렸다.
  둘은 해야 할 일이 정반대인데(키 교체 vs 모델 권한·이름), 서버 로그에도 같은 문장만 남아 되짚을 수 없었다.
  → 401/403을 나눠 상태코드·API 오류코드(invalid_api_key·model_not_found 등)·request id·모델명·키 모양
  (길이와 접두어. 값은 절대 안 남긴다)을 문구에 담고, api.py가 원래 예외를 exc_info로 로그에 남긴다.
- **Render 대시보드에 키를 붙여 넣었는데 계속 401**
  → 값에 따옴표나 앞뒤 공백·줄바꿈이 같이 들어가면 키는 맞아도 거부된다(길이만 다르게 보인다).
  키에는 원래 그런 문자가 없으므로 `llm.api_key()`가 떼어 내고 쓰며, 떼어 냈으면 로그로 알린다.
- **키가 틀린 줄 모르고 /draft를 불러야 502를 본다**
  → `/health?deep=true`의 `llm`이 설정만 보고 `configured: true`라고 했다(실제 호출은 비용이라 안 했음).
  모델 목록 조회(models.list)는 토큰 비용이 없으므로 이걸로 `key_ok`를 확인한다. 거부면 status=degraded.
  조회가 아예 안 되면(네트워크) `key_ok: null` — 키가 나쁘다고 단정하지 않는다.
- **생성은 되는데 평가만 '평가불가'**
  → 생성은 `OPENAI_MODEL`(기본 gpt-4o-mini), 평가는 `EVAL_MODEL`(기본 gpt-4o)로 모델이 다르다.
  키에 gpt-4o 권한만 없으면 평가 호출만 403인데, evaluate()가 예외를 삼켜 '평가불가'로만 보인다.
  또 `os.environ["OPENAI_API_KEY"]`라 키가 없으면 KeyError가 나 원인이 'OPENAI_API_KEY' 한 줄뿐이었다.

## API 오류 처리
- **GPT·OpenSearch가 실패하면 프론트에 '서버에 연결할 수 없어요'만 뜸**
  → 처리 안 된 예외는 500이 되는데, 이 응답은 CORS 미들웨어 바깥에서 만들어져 CORS 헤더가 없다. 브라우저는
  본문을 못 읽고 연결 실패로 본다. api.py의 exception_handler로 LLMError(502/503/504)·OpenSearchException(503)·
  ConfigError(503)를 {"detail": 안내 문구}로 돌려준다(이 응답은 CORS 헤더가 붙는다).
- **수정 호출만 실패했는데 1차 초안까지 사라짐**
  → 수정 generate가 try 밖이라 예외가 /draft 전체를 죽였다. 이제 1차 초안을 최종본으로 두고 경고.
- **OpenAI 호출이 멈추면 /draft가 10분 가까이 붙잡힘**
  → openai 라이브러리 기본 timeout은 600초·재시도 2회. LLM_TIMEOUT(기본 90초)·LLM_MAX_RETRIES(기본 1)로 줄였다.
- **OpenSearch가 끊겼는데 /notice가 '공지를 찾을 수 없습니다'(404)**
  → _get_notice가 모든 예외를 None으로 삼켰다. /notice는 _fetch_notice(없음=None, 연결 오류=예외→503)를 쓴다.
- **오프라인 테스트가 실제 OpenSearch에 붙어 조용히 통과함**
  → 셸에 OPENSEARCH_* 가 있으면 가짜를 빠뜨린 테스트도 실제 서버로 간다(/notice 테스트가 그랬다). conftest의
  autouse fixture가 get_client·Bedrock 클라이언트를 막아 두어, 빠뜨리면 바로 실패한다.
- **ingest-csv를 다시 돌리면 그 공지들의 임베딩이 지워짐**
  → client.index가 문서를 통째로 덮어쓴다. collect·ingest-csv 끝에 새 공지 임베딩을 자동으로 채운다(--no-embed로 생략).
  collect는 이미 있는 공지를 건너뛰어 해당 없음.

## 검색창(/search)
- **'헤데라'를 검색하면 '데'·'라'만 있는 공지까지 78건이 걸림**
  → 사전에 없는 이름이 형태소 조각('헤'+'데')으로 쪼개지고, 기본 검색(OR)은 조각 하나만 맞아도 넣는다.
  AND는 문장형 검색을 0건으로 만든다. 1위 점수 50% 미만을 빼면(min_score) 헤데라 14건·메가이더 4건, 전부 정답.
- **min_score와 카테고리별 건수**
  → min_score는 집계(aggs)에도 적용된다. 카테고리는 query filter가 아니라 post_filter로 걸어야 카테고리별 건수가
  그 카테고리를 눌렀을 때의 total과 같다(컷 기준 1위 점수도 카테고리와 무관하게 전체에서 잡는다).
- **벡터 결과를 본 목록에 섞으면 건수를 정할 수 없음**
  → kNN은 항상 k건을 돌려주고, 정답·오답 유사도 분포가 겹쳐 절대 기준이 없다(코인명 정답 0.2~0.3, 오답 최고 0.15~0.36).
  본 목록은 BM25, 의미 검색은 '비슷한 공지'로 따로. 한 단어(코인명)에는 다른 코인 공지만 끌려와 붙이지 않는다.
- **uvicorn --reload 뒤 첫 검색이 3초 넘게 걸림**
  → 코드가 바뀌어 프로세스가 다시 뜨면 Bedrock 클라이언트(boto3)를 새로 만든다. 이후는 0.3~0.6초.
- **발췌에서 '헤'만 강조되거나 '헤 데'로 끊겨 보임**
  → 형태소 조각 단위로 강조된다. 붙어 있는 `</em><em>`은 합쳐 준다(search._snippet). 완전한 해결은 아님.
- **쪽 번호를 크게 주면(page=2000) /search가 500**
  → OpenSearch는 from+size가 max_result_window(기본 1만)를 넘으면 오류. API에서 page×size > 1만이면 422로 막는다.
- **/ui 첫 화면(검색 전)에 정렬 버튼·이전/다음 버튼이 보임**
  → `.bar`·`.pager`에 준 `display:flex`가 `hidden` 속성(브라우저 기본 display:none)을 덮어썼다.
  `[hidden]{display:none !important}` 한 줄로 해결. hidden으로 숨기는 요소에 display를 줄 때 주의.

## 검색 / 참고 공지 선택
- **BM25 상위가 오래된 공지로 채워짐(에어드랍 1위가 2023년 등)**
  → 비슷한 공지가 동점으로 많고, 사유 문구가 옛 제목 양식('…업그레이드로 인한…')과 더 잘 맞는다.
  동점은 최신순(sort=score), 같은 유형 구절이 제목에 있는 최신 10건을 별도 풀로 합치고,
  재정렬에 최신성 가중(유형 있음 0.5/0.5, general 0.8/0.2).
- **'유의촉구 보안 사고' 검색 1위가 PR 공지('7년 연속 보안사고 0건')**
  → 키워드만 겹침. 후보 제목을 같은 라우팅 규칙으로 판별해 유형이 다르면 뒤로(tier 3).
- **같은 유형 공지인데 카테고리 태그가 제각각**(유의촉구 및 입출금 중단: [안내,입출금] / [안내]만)
  → 유형 일치는 태그가 아니라 제목으로 판별. 2개 카테고리 선택 시 1차 카테고리 검색도 합친다.
- **업데이트된 공지('…안내 (09/12 재개)')를 참고하면 재개 안내까지 본뜰 위험**
  → 빗썸은 재개·연기·정상화 때 같은 공지를 고친다: 제목 끝에 꼬리표를 붙이고, 본문 **위에** `<hr>`로
  구분한 안내를 쌓는다. 수집(html_to_text)에서 태그가 지워져 raw_text엔 구분선이 없다.
  실측 2,439건: 업데이트는 항상 원문 위(아래에 붙은 사례 0건), 원문은 마지막 '안녕하세요'부터.
  `factcheck.original_version()`으로 꼬리표·위쪽 블록을 떼고 최초 버전만 참고. '(09/10 오후 7시~)'는 원래 제목이라 유지.
  입력에 재개 일시가 없는데 '재개합니다/재개되었습니다'가 초안에 있으면 guard error.
  (장기적으로는 수집 시 `<hr>`을 구분 표시로 남기면 더 정확하다 — 재수집/재파싱 필요)
- **공시 '재산상 이익 제공' 공지는 본문이 비어 있음**(len 0~7, 표/첨부 이미지로 추정)
  → 본문 100자 미만은 참고 공지 후보에서 제외. 해당 유형은 참고 없이 일반 형식 + 경고.
- **새 코인명을 검색어에 넣으면 같은 코인의 옛 공지가 끌려와 네트워크·일정이 새어 들어옴**
  → 검색어에서 입력 코인명·티커를 뺀다.
- **코인명이 들어간 제목 match_phrase가 안 맞음**('멀티버스엑스(EGLD) 입출금 일시 중지 안내')
  → Nori `decompound_mode: mixed`가 복합어를 분해해 구절 위치가 어긋남. 일반 구절('유의촉구')은 정상.
  코인명 포함 제목은 `match` + `operator: and` 로 찾는다.
- **sort에 _score를 넣었는데 _score가 null**
  → `track_scores: true` 필요.
- **raw_text로 집계(aggs)하면 400 'Text fields are not optimised…'**
  → text 필드는 집계 불가. 개수는 exists 쿼리나 scroll로 센다.

## 저장소 / 보안
- **.gitignore에 `./.env`라고 써서 .env가 무시되지 않음**
  → gitignore는 `./` 접두어를 지원하지 않는다. `.env`(또는 `/.env`)로 써야 한다. 첫 커밋에 .env가 들어가
  원격 히스토리에 남았으므로 키는 폐기·재발급이 필요(히스토리 재작성은 별도 판단).
  같은 이유로 `./tests/**`, `./data/**`, `./src/notice_ai/prompts/**`도 실제로는 무시되지 않는다
  (prompts·data는 실행에 필요하니 무시하지 않는 것이 맞다).

## 환경 / 실행 (Windows, 추가)
- **Windows에서 `PYTHONPATH=src:tests`가 안 먹음**
  → Windows 파이썬의 경로 구분자는 `;`. `PYTHONPATH="src;tests"`.
- **PowerShell에서 `python -c "..."` 안의 따옴표가 사라짐**(SyntaxError)
  → PowerShell 5.1이 네이티브 인자에서 따옴표를 벗긴다. Git Bash heredoc(`python - <<'EOF'`)을 쓴다.
- **Git Bash에서 `gh issue create --title "/draft …"`의 제목이 `C:/Program Files/Git/draft …`로 바뀜**
  → MSYS가 `/`로 시작하는 인자를 Windows 경로로 바꾼다(이슈 #4 제목이 이렇게 올라갔었음).
  `MSYS_NO_PATHCONV=1 gh …`로 실행하거나, 긴 텍스트는 `--body-file`처럼 파일로 넘긴다.
## 배포(Render) / 시간대
- **요청문의 '내일'이 오늘 날짜로 풀림 (KST 자정~오전 9시에만)**
  → 서버가 UTC로 돈다(Render 기본). `datetime.now()`가 한국보다 하루 뒤.
  → 시각 기준은 `notice_types.now_kst()`를 쓴다. 한국은 서머타임이 없어 UTC+9 고정이라
  tzdata 없이 계산한다. 로컬 PC(한국 시간)에서는 안 드러나고 배포해야 보인다.
- **`?token=` 리다이렉트가 평문 http로 나가 토큰 쿠키가 한 번 암호화 없이 전송됨**
  → Render는 TLS를 앞단에서 끝내고 앱에는 평문으로 넘기는데, uvicorn이 그 앞단을 믿지
  않아 요청을 http로 본다.
  → 리다이렉트를 경로만 적는 상대 주소로 쓰고, 실행 명령에 `--forwarded-allow-ips='*'`.
- **블루프린트가 대시보드에서 고친 환경변수를 되돌림**
  → `render.yaml`에 `value:`로 적은 값은 블루프린트가 계속 강제한다.
  → 환경마다 다른 값(계정·자격증명)은 `sync: false`로 두거나, 코드에 기본값이 있으면 아예 안 적는다.

## LLM 설정
- **평가 모델만 바꾸려는데 초안 생성까지 바뀜 / 추출이 초안과 같은 모델에 묶임**
  → `OPENAI_MODEL` 하나를 여러 용도가 나눠 쓰고 있었다.
  → 역할별로 고른다(`llm.for_role`): `EXTRACT_MODEL`·`DRAFT_MODEL`·`EVAL_MODEL`.
  안 주면 예전 환경변수를 물려받으므로 쓰던 설정은 안 깨진다.
- **provider 분기를 한쪽만 고쳐 평가와 생성이 어긋남**
  → `llm.get_llm()`과 `evaluator._eval_llm()`에 같은 로직이 두 벌 있었다.
  → `for_role` 한 곳으로 합쳤다. 새 provider나 역할은 여기만 고친다.

## LLM 추출(/extract)
- **요청문에 없는 값을 채움 (`resume_at="미정"`, `law_clause="[조항 확인 필요]"`)**
  → 항목 설명(필드 질문)에 든 보기·안내 문구를 LLM이 지시로 읽는다.
  → 프롬프트로 막으면 어떤 요청문에선 먹히고 어떤 요청문에선 안 먹힌다(지시 준수는 확률적).
  가릴 수 있는 것은 코드로 막는다: 자리표시자만 든 값, 요청문에 근거 없는 '미정'을 버린다.
  뽑은 값도 사람이 친 값과 같은 검사(`field_problem`)를 거치게 해 둔 것이 실제로
  빈 코인명(`{"name":"", "ticker":"HBAR"}`)을 걷어냈다.
