"""참고 공지 마스킹 — 이 프로젝트 대원칙이 코드로 서 있는 자리.

    과거 공지는 형식과 문체의 예시일 뿐 사실 출처가 아니다.

마스킹이 새면 LLM 이 남의 공지에 있던 날짜·조항·코인을 그대로 베껴 쓴다. 그래서 여기서
확인하는 것은 '마스크 모양이 예쁜가'가 아니라 **사실값이 하나도 안 남았는가**다.
"""

from __future__ import annotations

import pytest

from notice_ai.factcheck import (
    MASK_CLAUSE,
    MIN_ORIGINAL,
    clauses,
    extract_dates,
    extract_times,
    mask_reference,
    original_version,
    paren_tickers,
)

REFERENCE = """안녕하세요. 빗썸입니다.
메가이더(MEGA) 입출금이 2026년 9월 20일 15:00부터 중지됩니다.
자세한 내용은 https://feed.bithumb.com/notice/1234 를 확인해 주세요.
가상자산이용자보호법 제10조 제1항에 따라 조치하였습니다.
메가이더 보유 회원께는 9월 1주차에 반영됩니다."""


@pytest.fixture(scope="module")
def masked() -> str:
    return mask_reference(REFERENCE, {"MEGA"})


@pytest.mark.parametrize("leaked", [
    "2026",          # 연도
    "9월 20일",       # 날짜
    "15:00",         # 시각
    "제10조",         # 조항
    "feed.bithumb",  # 링크
    "메가이더",        # 코인 한글명
    "MEGA",          # 티커
    "1주차",          # 차수
])
def test_사실값이_하나도_안_남는다(masked, leaked):
    assert leaked not in masked, f"'{leaked}' 가 마스킹을 통과했다"


def test_형식과_어투는_남는다():
    """값만 지우고 문장 구조는 남겨야 참고 자료로 쓸 수 있다."""
    out = mask_reference(REFERENCE, {"MEGA"})
    assert "안녕하세요. 빗썸입니다." in out
    assert "입출금이" in out and "중지됩니다" in out
    assert "가상자산이용자보호법" in out      # 법 이름은 사실값이 아니라 고정 문구
    assert MASK_CLAUSE in out


def test_티커만_따로_쓰인_곳도_가린다():
    out = mask_reference("MEGA 입출금을 중지합니다.", {"MEGA"})
    assert "MEGA" not in out


def test_티커를_안_줘도_괄호_티커는_찾아낸다():
    """색인 tickers 필드가 비어 있는 옛 문서도 있다."""
    out = mask_reference("메가이더(MEGA) 입출금 중지", ())
    assert "MEGA" not in out and "메가이더" not in out


def test_마스킹은_여러_번_해도_같다():
    once = mask_reference(REFERENCE, {"MEGA"})
    assert mask_reference(once, {"MEGA"}) == once


def test_빈_입력에도_안_터진다():
    assert mask_reference("", ()) == ""


# ── 사실값 뽑기(초안 검증에 쓰는 쪽) ──────────────────────────────────────
def test_조항을_정규화해서_모은다():
    assert clauses("제10조 제1항에 따라") == {"제10조제1항"}


def test_날짜와_시각을_뽑는다():
    assert extract_dates("2026년 9월 20일") == [(2026, 9, 20, None, "2026년 9월 20일")]
    assert extract_times("15:00") == [(15, 0, "15:00")]
    assert extract_times("오후 3시") == [(15, 0, "오후 3시")]


def test_시간_단위는_시각이_아니다():
    """'2시간 지연'의 2시간은 시각이 아니다."""
    assert extract_times("약 2시간 지연되고 있습니다") == []


def test_괄호_티커만_골라낸다():
    got = paren_tickers("메가이더(MEGA) 입출금 (KST 기준) 중지")
    assert got == {"MEGA"}, "KST 같은 일반 약어가 코인으로 섞였다"


# ── 업데이트 블록 떼어내기 ──────────────────────────────────────────────
def test_덧붙은_업데이트를_떼고_최초_버전을_되살린다():
    title = "메가이더(MEGA) 입출금 중지 안내 (09/20 재개)"
    body = (
        "안녕하세요. 빗썸입니다.\n메가이더(MEGA) 입출금이 재개되었습니다.\n\n"
        "안녕하세요. 빗썸입니다.\n메가이더(MEGA) 네트워크 업그레이드로 입출금을 중지합니다. "
        "중지 시점은 2026년 9월 18일 15:00이며, 재개 시점은 네트워크 안정화 후 별도 "
        "안내드립니다. 회원님의 양해를 부탁드립니다."
    )
    got_title, got_body, notes = original_version(title, body)
    assert got_title == "메가이더(MEGA) 입출금 중지 안내"
    assert "재개되었습니다" not in got_body
    assert "네트워크 업그레이드로 입출금을 중지합니다" in got_body
    assert len(notes) == 2


def test_원문이_너무_짧으면_자르지_않는다():
    """잘라낸 쪽이 짧으면 '안녕하세요'가 본문 중간의 인용일 수 있다. 판단을 미룬다."""
    body = "안녕하세요.\n재개했습니다.\n\n안녕하세요. 짧은 원문입니다."
    _, got_body, notes = original_version("제목", body)
    assert got_body == body
    assert notes == []
    assert len("안녕하세요. 짧은 원문입니다.") < MIN_ORIGINAL


def test_업데이트가_없는_공지는_그대로_둔다():
    title, body = "메가이더(MEGA) 입출금 중지 안내", "안녕하세요. 빗썸입니다.\n중지합니다."
    assert original_version(title, body) == (title, body, [])
