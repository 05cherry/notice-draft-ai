"""실제 LLM으로 /extract 를 확인한다 (수동 검사, 돈이 든다).

    py tests/try_extract.py            # 전부
    py tests/try_extract.py 2 5        # 2·5번만

.env 의 OPENAI_API_KEY·LLM_PROVIDER 를 읽는다. 호출 1건에 gpt-4o-mini 기준 몇 원 수준.

이 검사는 '돌아가는가'가 아니라 **뽑지 말아야 할 것을 안 뽑는가**를 본다.
값을 못 뽑는 건 사람이 채우면 그만이지만, 없는 값을 지어내 칸을 채우면 초안까지 틀어진다.
expect(있어야 할 값) 보다 forbid(없어야 할 항목) 가 중요하다.
"""

from __future__ import annotations

import os
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

# .env 읽기(라이브러리 없이)
for line in (ROOT / ".env").read_text(encoding="utf-8").splitlines() if (ROOT / ".env").exists() else []:
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))

from notice_ai.extract import extract_inputs   # noqa: E402

NOW = datetime(2026, 9, 20)      # 일요일 — '내일'은 9/21(월), '다음 주 월요일'은 9/28

# expect: 이 값이 나와야 한다(부분 문자열로 비교)   forbid: 이 항목이 나오면 안 된다
CASES = [
    dict(
        name="기본 — 세 항목이 또렷한 요청문",
        cats=["입출금"],
        text="헤데라(HBAR) 네트워크 점검 때문에 9/25 15시부터 입출금 일시 중지",
        expect={"coins": "헤데라(HBAR)", "suspend_at": "2026-09-25 15:00"},
        forbid=["resume_at", "upgrade_at", "law_clause"],   # 셋 다 요청문에 없다
        why="날짜 하나를 일시 항목 전부에 넣으면 안 된다(데모 규칙이 실제로 이 실수를 했다)",
    ),
    dict(
        name="티커만 — 한글명을 지어내면 안 된다",
        cats=["입출금"],
        text="HBAR 입출금 9/25 15시부터 중단, 네트워크 점검",
        expect={"suspend_at": "2026-09-25 15:00"},
        forbid=["coins"],
        why="'헤데라'를 지어내면 factcheck가 못 잡는 거짓이 초안에 들어간다",
    ),
    dict(
        name="상대 날짜 — 오늘 기준으로 계산",
        cats=["입출금"],
        text="메가이더(MEGA) 내일 오후 3시부터 입출금 중지, 체인 업그레이드",
        expect={"coins": "메가이더(MEGA)", "suspend_at": "2026-09-21 15:00"},
        forbid=["resume_at", "upgrade_at"],
        why="'내일'을 오늘(2026-09-20) 기준으로 풀어야 한다. 재개 얘기는 없으므로 "
            "resume_at='미정'을 채우면 안 된다(실제 GPT가 두 번 그랬다)",
    ),
    dict(
        name="연도 없음 — 가장 가까운 날로",
        cats=["입출금"],
        text="비너스(XVS) 10/2 09:00 입출금 재개 예정, 9/28 18:00 중지",
        expect={"coins": "비너스(XVS)", "resume_at": "2026-10-02 09:00", "suspend_at": "2026-09-28 18:00"},
        forbid=[],
        why="두 일시가 각자 제자리(중지/재개)로 가야 한다",
    ),
    dict(
        name="미정 — 모른다고 적은 것도 정보다",
        cats=["입출금"],
        text="수이(SUI) 9/22 10시부터 입출금 중지. 재개 시점은 미정",
        expect={"suspend_at": "2026-09-22 10:00", "resume_at": "미정"},
        forbid=[],
        why="'미정'은 빈 값과 다르다. 초안에서 '추후 안내'로 쓰인다",
    ),
    dict(
        name="뽑을 것 없음 — 빈 손으로 돌아와야 한다",
        cats=["안내"],
        text="공지 하나 써줘",
        expect={},
        forbid=["coins", "reason", "links", "law_clause"],
        why="막연한 요청에 값을 지어내면 안 된다. 특히 항목 설명의 보기('예) 메가이더(MEGA)')를 "
            "요청문의 값으로 착각해 베끼면 안 된다 (topic은 서버가 요청문으로 채운다)",
    ),
    dict(
        name="카테고리 2개 — 두 파트가 같은 코인을 쓴다",
        cats=["안내", "입출금"],
        text="네오(NEO) 시세 급변동 유의 촉구 및 9/23 14:00 입출금 일시 중단",
        expect={"coins": "네오(NEO)", "suspend_at": "2026-09-23 14:00"},
        forbid=[],
        why="두 파트에 걸친 항목은 categories 에 둘 다 찍혀야 한다",
    ),
    dict(
        name="요청문에 링크가 없다 — 지어내면 안 된다",
        cats=["안내"],
        text="이용약관 개정 안내, 10/1 시행. 결제 조항이 바뀝니다",
        expect={},
        forbid=["links"],
        why="없는 URL을 지어내면 사용자가 눈치채기 어렵다",
    ),
]


def run(case: dict) -> tuple[int, int, list[str]]:
    out = extract_inputs(case["cats"], text=case["text"], now=NOW)
    got = {f["field"]: f for f in out.fields}
    ok, bad, notes = 0, 0, []

    for name, want in case["expect"].items():
        if name not in got:
            bad += 1
            notes.append(f"  ✗ {name}: 못 뽑음 (기대: {want})")
        else:
            raw = got[name]["value"]
            flat = (", ".join(f"{c['name']}({c['ticker']})" for c in raw)
                    if isinstance(raw, list) and raw and isinstance(raw[0], dict) else str(raw))
            if want in flat:
                ok += 1
                notes.append(f"  ✓ {name}: {flat}")
            else:
                bad += 1
                notes.append(f"  ✗ {name}: {flat}  (기대: {want})")

    for name in case["forbid"]:
        if name in got:
            bad += 1
            notes.append(f"  ✗ {name}: 없어야 하는데 뽑혔다 → {got[name]['display']}")
        else:
            ok += 1

    # 기대도 금지도 아닌 항목. 틀린 건 아니지만 눈으로 봐 둘 값어치가 있다
    extra = [n for n in got if n not in case["expect"] and n not in case["forbid"] and n != "topic"]
    for n in extra:
        notes.append(f"  · 덤: {n} = {got[n]['display']}")
    for r in out.rejected:
        notes.append(f"  · 형식 불합격(칸은 비움): {r['field']} = {r['value']!r} — {r['problem']}")
    return ok, bad, notes


def main() -> int:
    picks = {int(a) for a in sys.argv[1:] if a.isdigit()}
    total_ok = total_bad = calls = 0
    print(f"LLM: {os.environ.get('LLM_PROVIDER', 'openai')} / {os.environ.get('OPENAI_MODEL', 'gpt-4o-mini')}")
    print(f"오늘(가정): {NOW:%Y-%m-%d}\n")

    for i, case in enumerate(CASES, 1):
        if picks and i not in picks:
            continue
        print(f"[{i}] {case['name']}")
        print(f"    요청문: {case['text']}")
        print(f"    왜 보나: {case['why']}")
        try:
            ok, bad, notes = run(case)
        except Exception as e:
            print(f"  ✗ 호출 실패: {type(e).__name__}: {e}\n")
            total_bad += 1
            continue
        calls += 1
        total_ok, total_bad = total_ok + ok, total_bad + bad
        print("\n".join(notes) or "  (뽑은 값 없음)")
        print()

    print(f"통과 {total_ok} · 실패 {total_bad} · LLM 호출 {calls}회")
    if total_bad:
        print("\n실패가 있으면 prompts/extract.txt 의 지시를 고친다. 값을 못 뽑는 것보다")
        print("없는 값을 지어내는 쪽(forbid 실패)이 훨씬 나쁘다 — 그쪽부터 본다.")
    return 1 if total_bad else 0


if __name__ == "__main__":
    raise SystemExit(main())
