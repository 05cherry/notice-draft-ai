"""유형 판별·입력 정규화 — 유형 스펙이 흔들리면 초안 전체가 흔들리는 자리.

여기서 지키는 것은 '어떤 유형이 나오는가'가 아니라 **판별 규칙의 성질**이다.
규칙에 낱말을 더 넣는 변경은 자유롭지만, 성질이 바뀌면 이 검사가 멈춘다.
"""

from __future__ import annotations

from datetime import datetime

import pytest

from notice_ai.notice_types import (
    MAX_CATEGORIES,
    coin_label,
    field_problem,
    is_unknown,
    normalize_coins,
    parse_datetime,
    render_datetime,
    route,
    types_for,
)

# 안내 카테고리의 airdrop family 가 지금 body_pattern 을 가진 유일한 짝이다.
TITLE_PLAN = "메가이더(MEGA) 에어드랍 지원 안내"
TITLE_PAID = "메가이더(MEGA) 에어드랍 지급 안내"
BODY_PAID = "안녕하세요. 빗썸입니다.\n메가이더(MEGA) 에어드랍 물량 지급이 완료되었습니다."
BODY_PLAN = "안녕하세요. 빗썸입니다.\n메가이더(MEGA) 에어드랍 물량을 지급할 예정입니다."
# 뜻은 '예정'인데 '예정'이라는 낱말이 없다 — 실제로 안내 279건 중 4건이 이 꼴이었다(#38).
BODY_NEITHER = "안녕하세요. 빗썸입니다.\n지급 시점은 확정되는 대로 별도 안내드리겠습니다."


def test_제목만으로_유형을_고른다():
    assert route("입출금", "메가이더(MEGA) 입출금 지연 안내")[0].subtype == "delay"
    assert route("안내", TITLE_PAID)[0].subtype == "airdrop_paid"


def test_규칙에_없는_제목은_general로_떨어진다():
    ntype, matched = route("안내", "빗썸 사옥 이전 안내")
    assert ntype.subtype == "general"
    assert matched == ""


def test_본문이_형제를_가리키면_제목을_뒤집는다():
    """제목은 '지원'(예정)인데 본문은 지급 완료 — 본문을 따른다."""
    assert route("안내", TITLE_PLAN, BODY_PAID)[0].subtype == "airdrop_paid"


def test_본문이_제목과_같은_편이면_그대로_둔다():
    assert route("안내", TITLE_PLAN, BODY_PLAN)[0].subtype == "airdrop_plan"


def test_본문이_어느_형제도_가리키지_않으면_제목을_살린다():
    """#38 — 본문 규칙은 제목을 뒤집을 때만 쓰고, 거부권으로는 쓰지 않는다.

    전에는 여기서 general 로 떨어뜨렸다. 그러면 빗썸이 규칙에 없는 표현을 쓴 공지가
    참고 후보에서 통째로 빠졌다. 제목은 맞았는데도.
    """
    ntype, matched = route("안내", TITLE_PLAN, BODY_NEITHER)
    assert ntype.subtype == "airdrop_plan", "본문 규칙이 거부권으로 되돌아갔다"
    assert matched == "에어드랍 지원"


def test_본문을_안_주면_본문_규칙은_아예_안_본다():
    assert route("안내", TITLE_PLAN)[0].subtype == "airdrop_plan"
    assert route("안내", TITLE_PLAN, None)[0].subtype == "airdrop_plan"


def test_general은_언제나_마지막_후보다():
    """general 의 pattern 은 비어 있어서, 앞에 오면 나머지를 모두 가린다."""
    for category in ("입출금", "안내", "거래유의", "공시"):
        subtypes = [t.subtype for t in types_for(category)]
        assert subtypes[-1] == "general", f"{category}: {subtypes}"
        assert subtypes.count("general") == 1


# ── 입력값 정규화 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("value", ["미정", "미확정", "추후 공지", "추후공지", "TBD", " 미정 "])
def test_모르는_값은_모르는_값으로_읽는다(value):
    assert is_unknown(value)


@pytest.mark.parametrize("value", ["2026-09-20 15:00", "메가이더(MEGA)", "", None, 0])
def test_아는_값과_빈_값은_모르는_값이_아니다(value):
    """is_unknown 은 '사용자가 모른다고 적었다'는 뜻이다. 빈 값은 '아직 안 적었다'로 따로 센다."""
    assert not is_unknown(value)


def test_일시_파싱은_시각이_있었는지도_같이_돌려준다():
    """날짜만 받았는데 '0시'로 렌더하면 없는 사실을 만들어 낸다."""
    dt, has_time = parse_datetime("2026-09-20 15:00")
    assert (dt.year, dt.month, dt.day, dt.hour, dt.minute) == (2026, 9, 20, 15, 0)
    assert has_time is True

    dt, has_time = parse_datetime("2026-09-20")
    assert (dt.hour, dt.minute) == (0, 0)
    assert has_time is False


def test_요일은_코드가_계산한다():
    """LLM 이 요일을 틀리는 문제 때문에 코드로 넣는다. 2026-09-20 은 일요일."""
    assert render_datetime("2026-09-20 15:00") == "2026.09.20(일) 오후 3:00(KST)"
    assert render_datetime("2026-09-20") == "2026.09.20(일)"
    assert render_datetime("2026-09-20 15:00", date_only=True) == "2026.09.20(일)"


def test_읽을_수_없는_일시는_그대로_보여_준다():
    assert render_datetime("미정") == "미정"


def test_일시가_아닌_값은_파싱하지_않는다():
    assert parse_datetime("미정") is None
    assert parse_datetime("내일 오후") is None


def test_코인_문자열을_이름과_티커로_나눈다():
    coins = normalize_coins("메가이더(MEGA), 비너스(XVS)")
    assert [c["ticker"] for c in coins] == ["MEGA", "XVS"]
    assert [c["name"] for c in coins] == ["메가이더", "비너스"]
    assert coin_label(coins) == "메가이더(MEGA), 비너스(XVS)"


def test_티커가_없는_코인_입력은_문제로_잡는다():
    """field_problem 은 normalize_coins 를 지난 값을 받는다."""
    assert field_problem("coins", normalize_coins("메가이더(MEGA)")) is None
    assert field_problem("coins", normalize_coins("메가이더")) is not None


def test_코인은_미정일_수_없다():
    assert field_problem("coins", "미정") is not None


def test_카테고리는_두_개까지():
    assert MAX_CATEGORIES == 2
