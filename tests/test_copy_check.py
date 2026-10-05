"""복사 판정에 쓰는 줄 다듬기 — #36 에서 '아무도 안 보는 줄'을 가른 기준.

복사 검사는 초안 줄이 참고 공지의 **고유 문장**과 너무 비슷하면 잡는다. 그런데 비교에 쓰는
줄은 이미 사실값이 가려져 있다(`_norm_line` → `mask_reference`). 조항 번호·날짜·코인은
각자 전용 검사가 보므로, 복사 검사만이 볼 수 있는 것은 **자리표시자 사이에 남은 글자**다.

'<날짜> <시각>' 처럼 자리표시자뿐인 줄은 베껴도 새로 틀릴 것이 없다.
'[조항 확인 필요] (약관의 명시, 설명과 개정)' 의 조항 제목은 아무도 안 본다 — 그게 이 이슈의 몫.
"""

from __future__ import annotations

import pytest

from notice_ai.copy_check import MIN_UNCOVERED, uncovered_text
from notice_ai.factcheck import MIN_COPY_LINE, _norm_line


def 덮였나(norm: str) -> bool:
    """자리표시자를 떼면 남는 글자가 거의 없다 = 복사 검사가 볼 것이 없다."""
    return len(uncovered_text(norm)) < MIN_UNCOVERED


@pytest.mark.parametrize("norm", [
    "<날짜> <시각>",
    "<날짜>",
    "<가상자산명>(<티커>)",
    "- <날짜> ~ <날짜>",
])
def test_자리표시자뿐인_줄은_볼_것이_없다(norm):
    assert 덮였나(norm)


@pytest.mark.parametrize("norm", [
    "[조항 확인 필요] (약관의 명시, 설명과 개정)",
    "<가상자산명>(<티커>) 입출금 중지",
    "회원님의 양해를 부탁드립니다",
])
def test_글자가_남는_줄은_봐야_한다(norm):
    assert not 덮였나(norm)


def test_줄머리를_떼고_남은_조항_마스크도_알아본다():
    """`_norm_line` 이 줄머리의 `[` 를 떼므로 조항 마스크는 '조항 확인 필요]' 꼴로도 온다.

    이걸 놓치면 조항 줄 전체가 '고유 문장'으로 세어져 숫자가 부풀어 오른다.
    """
    norm = _norm_line("■ 제3조 (약관의 명시, 설명과 개정)", ())
    assert norm.startswith("조항 확인 필요]"), f"줄머리 처리가 바뀌었다: {norm!r}"
    assert uncovered_text(norm) == "약관의명시설명과개정"


def test_본문에_그냥_쓰인_말은_마스크로_안_센다():
    """닫는 괄호를 필수로 둔 이유. '조항 확인 필요' 라고 적힌 안내문까지 지우면 안 된다."""
    assert uncovered_text("조항 확인 필요 여부를 알려 주세요") != ""


def test_날짜_줄은_정규화하면_짧아진다():
    """'2026년 9월 20일 15:00' → '<날짜> <시각>'. 9글자라 MIN_COPY_LINE 밑으로 내려간다."""
    norm = _norm_line("- 2026년 9월 20일 15:00", ())
    assert norm == "<날짜> <시각>"
    assert len(norm) < MIN_COPY_LINE
    assert 덮였나(norm)


def test_기호만_있는_줄은_빈_값이_된다():
    assert uncovered_text("- ·  ※") == ""
