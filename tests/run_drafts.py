"""카테고리별 초안 자동 테스트 러너.

각 카테고리마다:
  1) DB에서 그 카테고리의 대표 공지 1건을 자동으로 뽑아 '기준 공지'로 사용
  2) 미리 정의한 문답값(answers)으로 초안 생성
  3) 고정값이 초안에 들어갔는지 / 경고가 뭔지 자동 점검 → ✅/⚠️ 요약
  4) 초안 전문은 tests/results/ 에 저장

실행:
    py tests/run_drafts.py                # 전체 카테고리
    py tests/run_drafts.py 입출금 거래유의   # 특정 카테고리만

주의: 케이스마다 GPT를 호출하므로 OpenAI 크레딧을 조금 쓴다.
환경변수(OPENSEARCH_*, OPENAI_API_KEY, LLM_PROVIDER, PYTHONPATH=src) 필요.
"""

from __future__ import annotations

import sys
from pathlib import Path

from notice_ai import config
from notice_ai.drafting import (
    _get_notice,
    _high_risk_values,
    _value_present,
    generate_draft,
)
from notice_ai.evaluator import evaluate
from notice_ai.opensearch_client import get_client
from notice_ai.search import bm25_search

# 카테고리별 테스트 시나리오(문답값). 실제 상황을 흉내 낸 가짜 입력이다.
# 특정 기준 공지를 고정하고 싶으면 "_base_url": "https://..." 을 케이스에 추가한다.
# 없으면 시나리오 내용(coin/action/reason)으로 검색해 가장 유사한 공지를 자동 선택한다.
CASES: dict[str, dict] = {
    "입출금": {
        "coin_kr": "메가이더", "ticker": "MEGA",
        "action": "입출금 일시 중단", "reason": "네트워크 업그레이드",
        "datetime": "2026-09-01 15:00",
    },
    "거래유의": {
        "coin_kr": "테스트코인", "ticker": "TSTC",
        "action": "가상자산 거래유의종목 지정",
        "reason": "유통량 관련 중요사항 미공시 확인",
        "datetime": "2026-09-01 15:00",
    },
    "거래지원종료": {
        "coin_kr": "테스트코인", "ticker": "TSTC",
        "action": "거래지원 종료", "reason": "거래유의종목 지정 사유 미해소",
        "datetime": "2026-09-10 15:00",
    },
    "마켓 추가": {
        "coin_kr": "넥소", "ticker": "NEXO",
        "action": "원화 마켓 추가", "reason": "신규 거래지원",
        "datetime": "2026-09-02 19:00",
    },
    "점검": {
        "action": "시스템 정기 점검", "reason": "서버 안정화 작업",
        "datetime": "2026-09-03 02:00",
    },
    "이벤트": {
        "coin_kr": "비트코인", "ticker": "BTC",
        "action": "거래 이벤트", "reason": "신규 회원 대상 리워드",
        "datetime": "2026-09-05 10:00",
    },
    "수수료 이벤트": {
        "action": "거래 수수료 할인 이벤트", "reason": "프로모션",
        "datetime": "2026-09-05 10:00",
    },
    "안내": {
        "action": "서비스 이용 관련 안내", "reason": "정책 변경",
        "datetime": "2026-09-04 10:00",
    },
    "업데이트": {
        "action": "앱 업데이트 안내", "reason": "기능 개선",
        "datetime": "2026-09-06 10:00",
    },
    "신규서비스": {
        "action": "신규 서비스 오픈 안내", "reason": "서비스 출시",
        "datetime": "2026-09-07 10:00",
    },
    "공시": {
        "coin_kr": "테스트코인", "ticker": "TSTC",
        "action": "프로젝트 공시", "reason": "재단 정보 업데이트",
        "datetime": "2026-09-08 10:00",
    },
    "후기": {
        "action": "이용 후기 안내", "reason": "참고용",
        "datetime": "2026-09-09 10:00",
    },
}

RESULTS_DIR = Path(__file__).resolve().parent / "results"


def pick_base_notice(category: str, answers: dict) -> dict | None:
    """기준 공지 선택.

    1) 케이스에 _base_url이 있으면 그 공지를 그대로 사용(사용자 지정 시뮬레이션).
    2) 없으면 시나리오 내용(coin/action/reason)으로 그 카테고리 안에서 검색해
       가장 유사한 공지를 기준으로 삼는다. → 실제 기능 2 흐름과 동일.
    """
    explicit = answers.get("_base_url")
    if explicit:
        return _get_notice(explicit)

    query = " ".join(
        str(answers.get(k, "")) for k in ("coin_kr", "action", "reason")
    ).strip()
    hits = bm25_search(query, {"category": category} if category else {}, size=1)
    return _get_notice(hits[0].source_url) if hits else None


def run_case(category: str, answers: dict, do_eval: bool = True) -> dict:
    base = pick_base_notice(category, answers)
    if base is None:
        return {"category": category, "status": "SKIP",
                "detail": "기준 공지를 찾지 못함(수집 부족 또는 검색 결과 없음)"}

    base_url = base["source_url"]
    clean_answers = {k: v for k, v in answers.items() if k != "_base_url"}
    result = generate_draft(base_url, clean_answers, category=category)

    missing = [v for v in _high_risk_values(clean_answers) if not _value_present(v, result.draft)]
    empty_draft = not result.draft.strip()

    status = "OK"
    if empty_draft:
        status = "FAIL"
    elif missing:
        status = "WARN"

    out = {
        "category": category,
        "status": status,
        "base_url": base_url,
        "base_title": base.get("title", ""),
        "referenced_count": len(result.referenced),
        "missing_values": missing,
        "warnings": result.warnings,
        "draft": result.draft,
        "eval": None,
    }

    # 평가 agent — 초안을 LLM이 채점
    if do_eval and not empty_draft:
        out["eval"] = evaluate(clean_answers, result.draft)
    return out


def main() -> None:
    RESULTS_DIR.mkdir(exist_ok=True)
    args = [a for a in sys.argv[1:]]
    do_eval = "--no-eval" not in args
    targets = [a for a in args if not a.startswith("--")] or list(CASES.keys())

    print(f"\n초안 자동 테스트 — {len(targets)}개 카테고리"
          f"{' (평가 포함)' if do_eval else ' (평가 생략)'}\n" + "=" * 60)
    summary = []
    for cat in targets:
        answers = CASES.get(cat)
        if answers is None:
            print(f"[{cat}] 정의된 시나리오 없음, 건너뜀")
            continue
        r = run_case(cat, answers, do_eval=do_eval)
        summary.append(r)

        mark = {"OK": "✅", "WARN": "⚠️", "FAIL": "❌", "SKIP": "⬜"}[r["status"]]
        print(f"\n{mark} [{cat}] {r['status']}")
        if r["status"] == "SKIP":
            print(f"    {r['detail']}")
            continue
        print(f"    기준공지: {r['base_title'][:40]}")
        print(f"    참고한 유사공지: {r['referenced_count']}건")
        if r["missing_values"]:
            print(f"    누락된 고정값: {r['missing_values']}")
        for w in r["warnings"]:
            print(f"    ⚠ {w}")
        # 평가 결과
        ev = r.get("eval")
        if ev is not None:
            if ev.error:
                print(f"    [평가] 실패: {ev.summary}")
            else:
                sc = ev.scores
                print(f"    [평가] {ev.verdict} (합계 {ev.total}/25)  {ev.summary}")
                for k, desc in [("format","형식"),("tone","어투"),
                                ("caution_fit","유의사항"),("no_fabrication","비지어냄"),
                                ("fixed_values","고정값")]:
                    if k in sc:
                        c = ev.comments.get(k, "")
                        print(f"        {desc} {sc[k]}/5 — {c}")
        # 초안 전문 저장
        out = RESULTS_DIR / f"{cat.replace(' ', '_')}.txt"
        out.write_text(r["draft"], encoding="utf-8")

    print("\n" + "=" * 60)
    counts: dict[str, int] = {}
    for r in summary:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("생성 요약:", "  ".join(f"{k}={v}" for k, v in counts.items()))
    if do_eval:
        verdicts: dict[str, int] = {}
        for r in summary:
            ev = r.get("eval")
            if ev and not ev.error and ev.verdict:
                verdicts[ev.verdict] = verdicts.get(ev.verdict, 0) + 1
        if verdicts:
            print("평가 요약:", "  ".join(f"{k}={v}" for k, v in verdicts.items()))
    print(f"초안 전문은 {RESULTS_DIR} 에 저장됨")


if __name__ == "__main__":
    main()