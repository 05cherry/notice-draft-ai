# 로컬에서 수집 테스트하기

환경변수를 매번 손으로 넣지 않도록 **한 번에 불러오는 스크립트**와, 그걸로
`collect`(공지 수집)를 **안전하게 테스트**하는 순서를 적는다.

> 요약: ① `env.local.ps1` 한 번 채운다 → ② 수집 전용 터미널에서 `.\collect.ps1 ...` 한 줄(env 로드+수집)
> → ③ 소량(`--max-pages 1`)부터, `--incremental`·`--gap`으로 안전하게.

---

## 1. 환경변수 한 번에 넣기

세션마다 `export`/`$env:`를 치는 대신, 템플릿을 복사해 값을 채우고 불러온다.
실제 파일(`env.local.ps1`, `env.sh`)은 비밀값이라 `.gitignore`에 있어 커밋되지 않는다.
커밋되는 건 `*.example` 템플릿뿐.

### PowerShell (기본)

```powershell
# 최초 1회: 템플릿 복사 후 값 채우기
Copy-Item env.local.ps1.example env.local.ps1
notepad env.local.ps1        # OPENSEARCH_PASSWORD 등 채우기

# 터미널 열 때마다 1회 (앞의 "점 + 공백" = dot-source, 현재 셸에 변수를 심는다)
. .\env.local.ps1
```

실행 정책에 막히면 그 터미널에서만 한 번 풀어 준다:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

### Git Bash

```bash
cp env.sh.example env.sh      # 최초 1회, 값 채우기
source env.sh                 # 터미널 열 때마다 1회
```

불러오면 끝에 `환경변수 로드 완료 ...` 한 줄이 찍힌다. 확인:

```powershell
echo $env:OPENSEARCH_ENDPOINT   # PowerShell
```
```bash
echo "$OPENSEARCH_ENDPOINT"     # bash
```

### 수집 전용 터미널: `collect.ps1` (권장)

**환경변수는 프로세스(터미널)마다 따로다.** A 터미널에서 `$env:`로 넣어도 B 터미널엔 안
보인다. 그래서 "env 넣는 터미널 / 수집 돌리는 터미널"을 나누면 수집 쪽은 변수가 비어
`ConfigError`가 난다 — **로드와 실행은 같은 셸 안에서 이어져야 한다.**

래퍼 `collect.ps1`은 그 둘(env 로드 → `collect`)을 한 프로세스에서 묶어 준다. 수집 전용
터미널을 새로 열고 **이 한 줄만** 치면 된다(뒤 인자는 그대로 `collect`로 넘어감):

```powershell
.\collect.ps1 --incremental --no-embed
.\collect.ps1 --category 입출금 --max-pages 1 --no-body --no-embed
```

환경변수 정의는 여전히 `env.local.ps1` 한 곳에만 있고, 래퍼는 그걸 불러 쓸 뿐이다.
(Git Bash면 래퍼 없이 `source env.sh && py -m notice_ai.cli collect --incremental --no-embed`)

**어떤 변수가 왜 필요한가**

| 변수 | 언제 필요 |
|---|---|
| `OPENSEARCH_ENDPOINT` / `_USER` / `_PASSWORD` | 수집·검색 (필수) |
| `PYTHONPATH=src`, `PYTHONIOENCODING=utf-8` | CLI 실행·한글 깨짐 방지 |
| `NOTICE_ALIAS` / `NOTICE_INDEX` | 색인 대상 고르기 (아래 2장) |
| `OPENAI_API_KEY`, `LLM_PROVIDER` | 초안 생성·맞춤법 (수집만 할 땐 불필요) |
| `AWS_*` | 수집 뒤 자동 임베딩·벡터 검색 (테스트는 `--no-embed` 권장) |

---

## 2. 색인 대상 확인

지금은 로컬·Render·깃 모두 테스트 환경이라 **기본값대로 `collect`하면 된다.** 별칭
`notices_live`가 가리키는 실인덱스로 들어가고, 그래도 괜찮다. 수집한 공지는 사전 재색인이
돌아도 새 인덱스로 복사돼 유지된다(인덱스 관리는 `docs/INDEX_OPS.md`).

지금 색인이 어디로 들어가는지 한 줄로 확인:

```powershell
py -c "from notice_ai import index_ref; print('target=', index_ref.target(), '/ concrete=', index_ref.concrete())"
```

`target`이 별칭(`notices_live`)이고 `concrete`가 그 별칭이 가리키는 진짜 인덱스
(`notices_20261002094607`처럼 시각이 붙은 이름)다.

> **(참고) 나중에 운영을 따로 가를 때만** — `env.local.ps1`에서 아래 두 줄의 주석을 풀어
> 별칭을 무시하고 테스트 전용 인덱스로 보낼 수 있다. 처음 한 번 `setup-index`로 만든다.
> ```powershell
> $env:NOTICE_ALIAS = ""
> $env:NOTICE_INDEX = "notices_test"
> # py -m notice_ai.cli setup-index
> ```

---

## 3. 수집 실행 (소량 → 전체)

빗썸은 **429(요청 과다)에 아주 민감**하다. 재시도로 밀어붙이면 차단이 더 길어진다
(→ `docs/GOTCHAS.md` 수집/스크래핑). 반드시 **작게 시작**한다.

아래는 `collect.ps1` 래퍼 기준(env를 자동으로 불러옴). 래퍼 없이 쓰려면 env를 먼저 불러온 뒤
`.\collect.ps1` 자리에 `py -m notice_ai.cli collect`를 넣으면 똑같다.

```powershell
# (0) 연결·차단 여부부터: 한 카테고리 1페이지(30건), 본문·임베딩 생략
.\collect.ps1 --category 입출금 --max-pages 1 --no-body --no-embed

# (1) 본문까지 소량
.\collect.ps1 --category 입출금 --max-pages 2 --no-embed

# (2) "새로 올라온 것만" 빠르게 받기 — 기존만 나오는 페이지에서 그 카테고리를 멈춘다
.\collect.ps1 --incremental --no-embed

# (3) 429가 잦으면 요청 간격을 늘린다(기본 2초 → 5초)
.\collect.ps1 --incremental --gap 5 --no-embed
```

### 자주 쓰는 옵션

| 옵션 | 뜻 |
|---|---|
| `--category 이름` | 한 카테고리만 (생략 시 전체 12개) |
| `--max-pages N` | 카테고리당 N페이지까지만 (테스트용, 1페이지=30건) |
| `--incremental` | **새 공지만**: 신규가 하나도 없는 페이지를 만나면 그 카테고리를 멈춤(최신순이라 그 뒤는 전부 이미 있음). 수백 페이지 헛도는 것·429 위험을 줄인다 |
| `--gap 초` | 요청 사이 최소 간격(기본 2.0). 429가 잦으면 늘린다 |
| `--no-body` | 본문 없이 목록만 색인(빠름, 429 위험↓). 단 본문 없는 공지는 유형 판별에 불리 |
| `--no-embed` | 수집 뒤 **자동 임베딩을 생략**. 버리는 테스트이거나 AWS 자격증명이 없을 때만 |

- **재실행은 안전하다.** 이미 색인된 공지(= `source_url` 존재)는 상세 요청도 건너뛴다.
- 평소 "밀린 새 공지만 채우기"에는 **`--incremental`**, 처음부터 전량을 담을 때만 옵션 없이 전체 순회.

#### 임베딩은 언제 도나

임베딩은 **수동이 기본이 아니다.** `--no-embed`를 안 붙이면 `collect`가 끝나면서 새 공지를
자동으로 임베딩한다. 수동 `embed`는 그걸 건너뛴 경우의 백필일 뿐이다.

| 경로 | 명령 | 언제 |
|---|---|---|
| **자동(기본)** | `.\collect.ps1 --incremental` | 실제로 쓸 데이터. 수집 끝에 새 공지만 Bedrock으로 임베딩 |
| **수동 백필** | `py -m notice_ai.cli embed` | `--no-embed`로 미뤘거나 빠진 게 남았을 때. 임베딩 **없는 것만** 처리, 재실행 안전 |

- **AWS 자격증명 필요:** 자동이든 수동이든 Bedrock(Titan)을 부르니 `env.local.ps1`에 AWS 키가
  있어야 한다. 없으면 수집은 되고 임베딩만 "건너뜀" 한 줄 찍고 넘어간다(수집은 안 깨짐) →
  나중에 키 넣고 `embed`로 채운다.
- **임베딩이 없어도** 기본 BM25 검색은 된다. 임베딩은 벡터 검색·유형 추정·"비슷한 공지"에만 쓰인다.
  그래서 "수집 되나"만 보는 단계에선 `--no-embed`로 꺼도 지장 없다.

### 결과 확인

```bash
py -m notice_ai.cli search "입출금 중단" --category 입출금
```

---

## 4. 막히면

| 증상 | 조치 |
|---|---|
| `ModuleNotFoundError: notice_ai` | `PYTHONPATH=src` 안 잡힘 → `env` 스크립트 다시 불러오기 |
| 한글이 `???`·깨짐 | `PYTHONIOENCODING=utf-8` (스크립트에 포함됨) |
| `ConfigError: OPENSEARCH_... 필요` | 환경변수 미로드 → `. .\env.local.ps1` |
| `429` 반복 | 멈추고 5~10분(길면 하룻밤) 대기, `--gap` 늘리기. 자세히는 `docs/GOTCHAS.md` |
| `buildId 추출 실패` | 사이트 차단 가능성 → 시간 두고 재시도 |

더 많은 함정: [docs/GOTCHAS.md](GOTCHAS.md) · 인덱스 운영(별칭 전환 등): [docs/INDEX_OPS.md](INDEX_OPS.md)
