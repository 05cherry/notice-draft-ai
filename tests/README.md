# 테스트

저장소에 **포함돼 있습니다.** 전에는 `.gitignore`로 빠져 있어서 다른 PC나 팀원이 받을 수
없었고, 세션마다 쓴 검사가 커밋되지 않은 채 사라졌습니다(#12).

## 돌리는 법

```bash
pip install -r requirements.txt     # pytest 포함
pytest                             # 저장소 어디서든
```

`pytest.ini`가 `pythonpath = src`를 잡아 주므로 `PYTHONPATH`를 따로 줄 필요가 없습니다.

```bash
pytest -q                          # 조용히
pytest tests/test_auth.py -q       # 한 파일만
pytest -k 별칭 -v                   # 이름으로 골라서
```

## 두 종류가 있습니다

| | `test_*.py` | `try_*.py` |
|---|---|---|
| 무엇 | 자동 검사 | 사람이 눈으로 보는 수동 검사 |
| 바깥 접속 | **없음** | OpenAI·OpenSearch |
| 자격증명 | 필요 없음 | `.env` 필요 |
| 돈 | 안 듦 | **듦** |
| `pytest` 가 줍는가 | 예 | 아니오(`python_files = test_*.py`) |

`try_*.py`는 직접 부릅니다.

```bash
py tests/try_extract.py            # 전부
py tests/try_extract.py 2 5        # 2·5번만
```

## `test_*.py`가 다루는 것

| 파일 | 지키는 것 |
|---|---|
| `test_notice_types.py` | 유형 판별 규칙의 성질. 본문 규칙은 제목을 뒤집을 때만 쓴다(#38) |
| `test_factcheck.py` | 참고 공지 마스킹 — 사실값이 하나도 안 남는가(프로젝트 대원칙) |
| `test_copy_check.py` | 복사 판정에 쓸 줄 고르기 — 자리표시자뿐인 줄은 뺀다(#36) |
| `test_index_ref.py` | 별칭 층 — 전환이 호출 한 번, 별칭을 인덱스로 착각하지 않기(#34·#47) |
| `test_auth.py` | 공유 토큰 — POST 가 303 으로 새지 않기(#46), 비ASCII 토큰에 500 안 내기 |
| `test_prune.py` | 쌓인 인덱스 치우기 — **지워선 안 될 것을 지우지 않기**(#53) |
| `test_block_sep.py` | `<hr>` 경계 보존과 원문 복원, 본문 다시 받기(#9) |

OpenSearch가 필요한 자리는 가짜 클라이언트로 대신합니다(`test_index_ref.py`의 `FakeClient`).

## 검사를 더할 때

**무엇이 깨지면 안 되는지**를 쓰고, 그다음 그게 실제로 잡히는지 확인합니다. 코드를 일부러
되돌려 놓고 검사가 멈추는지 보는 겁니다 — 멈추지 않으면 그 검사는 아무것도 지키지 않습니다.
이 저장소의 검사는 전부 그렇게 확인했습니다.

검사 이름은 한국어 문장으로 씁니다. `pytest -v` 출력이 그대로 '무엇을 지키는지' 목록이 됩니다.

## 산출물

`tests/results/`는 옛 러너(`run_drafts.py`)가 쓰던 자리입니다. 지금은 쓰지 않고
`.gitignore`로 제외합니다. 새로 만드는 산출물도 그 아래에 둡니다.
