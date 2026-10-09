"""작성 부서 분류표로 넣은 유형 — 거래지원종료·마켓 추가·점검 (#10).

제목은 전부 `docs/NOTICE_TAXONOMY.md`에 적힌 **실제 대표 공지 제목**이다. 꾸며 낸 제목으로
검사하면 규칙이 실제 공지에 걸리는지는 알 수 없다.

여기서 지키는 것은 셋이다.
  1. 부서 중분류가 우리 유형으로 간다            (유형을 넣은 이유)
  2. 새 규칙이 기존 유형을 가로채지 않는다        (넣으면서 깨질 수 있는 것)
  3. 유형마다 묻는 항목이 정해져 있다            (general 과 달라지는 지점)
"""

from __future__ import annotations

import pytest

from notice_ai.notice_types import (
    SUSPEND_REASONS,
    TARGET_CATEGORIES,
    TYPES,
    catalog,
    field_choices,
    get_type,
    required_for,
    resolve,
    route,
)

# (카테고리, 실제 공지 제목, 가야 할 subtype) — 부서 시트의 '소분류(최신 공지 제목)' 그대로
실제_제목 = [
    ("거래지원종료", "알에스에스쓰리(RSS3) 거래지원 종료", "terminate"),
    # 종료 일정 변경(3건)도 같은 유형으로 보낸다 — 묻는 항목이 같고 일시만 다시 받으면 된다
    ("거래지원종료", "셀프 체인(SLF) 거래지원 종료 (출금 지원 종료 일시 변경)", "terminate"),
    ("마켓 추가", "텔러파이낸스(DEBIT) 원화 마켓 추가", "market_add"),
    ("마켓 추가", "USDT 마켓 10종 페어 추가 및 BTC 마켓 4종 페어 추가", "market_add"),
    ("점검", "정부24 점검으로 인한 주민등록증 진위확인 서비스 일시 중단 안내", "external_maintenance"),
    ("점검", "KB국민은행 시스템 점검 작업으로 인한 서비스 일시 중단 안내", "external_maintenance"),
    ("점검", "금융결제원 시스템 점검으로 인한 고객확인, 실명계좌 등록 서비스 일시 중단 안내 (완료)",
     "external_maintenance"),
    ("점검", "빗썸 서비스 점검 안내 (완료)", "maintenance"),
    ("점검", "거래 서비스 점검 안내 (완료)", "partial_maintenance"),
    ("점검", "빗썸 포인트샵 서비스 점검 안내 (종료)", "partial_maintenance"),
    ("점검", "빗썸 고객센터 채팅상담 서비스 일시 중지 안내", "partial_maintenance"),
]


@pytest.mark.parametrize("category,title,subtype", 실제_제목)
def test_부서_중분류의_대표_제목이_그_유형으로_간다(category, title, subtype):
    assert route(category, title)[0].subtype == subtype


# 새 규칙을 넣으면서 깨질 수 있는 것 — 기존 유형의 대표 제목도 같은 시트에서 가져왔다
@pytest.mark.parametrize("category,title,subtype", [
    ("입출금", "멀티버스엑스(EGLD) 입출금 일시 중지 안내 (09/16 재개)", "suspend"),
    ("입출금", "제로지(0G) 입출금 일시 지연 안내 (정상화)", "delay"),
    ("거래유의", "바운스빗(BB) 거래유의종목 지정 해제", "release"),
    ("거래유의", "질리카(ZIL) 거래유의종목 지정 연장", "extend"),
    ("거래유의", "헤미(HEMI) 거래유의종목 지정", "designate"),
    ("안내", "서비스 일시 순단 현상 안내 (업데이트)", "service_incident"),
    ("안내", "샌드박스(SAND) 유의촉구 안내", "caution_urge"),
])
def test_기존_유형은_그대로다(category, title, subtype):
    assert route(category, title)[0].subtype == subtype


# 실측(색인 4,650건)으로 규칙이 빠뜨린 것을 찾아 넓힌 자리. 제목은 전부 색인에 있는 실제 공지다.
@pytest.mark.parametrize("category,title,subtype", [
    # '상장'은 2022~2023년 공지가 쓰는 옛 표현이다. 이게 없어서 마켓 추가 general 이 31%였다
    ("마켓 추가", "힙스(HIBS), 게이머코인(GHX) BTC 마켓 상장 및 이벤트 안내(상장 완료)", "market_add"),
    ("마켓 추가", "울트라(UOS) BTC 마켓 상장 및 에어드랍 이벤트 지급 안내", "market_add"),
    ("마켓 추가", "이포스(WOZX), 드래곤베인(DVC) 상장 및 에어드랍 이벤트 안내 (상장 완료)", "market_add"),
    # 외부 기관 일은 '…으로 인한' 없이 '<기관> 시스템 작업 안내' 꼴로도 온다
    ("점검", "SC 제일은행 대외계 시스템 작업 안내", "external_maintenance"),
    ("점검", "1원 이체 서비스 관련 일부 은행 시스템 작업 안내", "external_maintenance"),
    ("점검", "KCB 휴대폰 본인확인 서비스 작업 안내", "partial_maintenance"),
    ("점검", "휴대폰 본인확인 서비스 작업 안내", "partial_maintenance"),
])
def test_실측으로_찾은_표현도_유형에_걸린다(category, title, subtype):
    assert route(category, title)[0].subtype == subtype


@pytest.mark.parametrize("category,title", [
    # 마켓 추가 카테고리지만 상장 공지가 아니다 — 이벤트 지급은 2차(이벤트 유형)가 가져갈 몫이다
    ("마켓 추가", "갈라(GALA) 이벤트 지급 안내"),
    ("마켓 추가", "어셈블프로토콜(ASM), 아이비피토큰(IBP) 이벤트 지급 안내"),
    # 점검 카테고리지만 점검이 아니라 장애다
    ("점검", "홈페이지, 모바일APP 접속 일시 불가 안내 (정상화)"),
])
def test_성격이_다른_공지는_general로_남긴다(category, title):
    """넓히다가 성격이 다른 공지까지 끌어오면 엉뚱한 틀을 들이댄다.

    '이벤트 지급 안내'에 마켓·거래 개시 일시를 물으면 작성자가 답할 수 없다.
    """
    assert route(category, title)[0].subtype == "general"


def test_상장_폐지는_마켓_추가가_아니다():
    """뜻이 반대다. 카테고리가 달라 섞일 일은 없지만 규칙이 그 말을 먹으면 안 된다."""
    import re

    from notice_ai.notice_types import get_type

    pat = get_type("마켓 추가", "market_add").pattern
    assert not re.search(pat, "알에스에스쓰리(RSS3) 상장 폐지 안내")
    assert re.search(pat, "텔러파이낸스(DEBIT) 원화 마켓 상장")


def test_전체_서비스_점검_규칙은_개별_서비스_점검을_가져가지_않는다():
    """'빗썸 포인트샵 서비스 점검'과 '빗썸 서비스 점검'은 달라야 한다.

    `(빗썸|전체)\\s*서비스\\s*점검` 으로 좁힌 이유가 이것이다. `서비스\\s*점검` 까지 받으면
    포인트샵·상담·거래 점검(65건)이 전부 '전체 서비스 점검'으로 가서, 작성자에게
    기관·대상 서비스를 묻지 않고 전체 점검 공지 틀을 들이댄다.
    """
    assert route("점검", "빗썸 서비스 점검 안내")[0].subtype == "maintenance"
    assert route("점검", "빗썸 포인트샵 서비스 점검 안내")[0].subtype == "partial_maintenance"


def test_외부_기관_점검은_개별_점검보다_먼저_잡힌다():
    """'정부24 점검으로 인한 …'은 개별 점검 규칙('점검')에도 걸리는 제목이다.

    순서가 뒤집히면 기관명(provider)을 묻지 않게 된다. 기관명은 본문에 반드시 들어가야 하고
    (exact=True) 재개 시점을 우리가 약속할 수 없어서 유형을 따로 둔 것이다.
    """
    t = route("점검", "정부24 점검으로 인한 주민등록증 진위확인 서비스 일시 중단 안내")[0]
    assert t.subtype == "external_maintenance"
    assert "provider" in t.required


@pytest.mark.parametrize("category,subtype,묻는것", [
    ("거래지원종료", "terminate", {"coins", "trade_end_at", "withdraw_end_at", "reason"}),
    ("마켓 추가", "market_add", {"coins", "market", "trade_open_at"}),
    ("점검", "external_maintenance", {"provider", "service", "maintenance_from", "maintenance_to"}),
    ("점검", "maintenance", {"maintenance_from", "maintenance_to", "maintenance_scope"}),
    ("점검", "partial_maintenance", {"service", "maintenance_from", "maintenance_to"}),
])
def test_유형마다_묻는_항목이_정해져_있다(category, subtype, 묻는것):
    t = get_type(category, subtype)
    assert t is not None
    assert set(t.required) == 묻는것
    assert t.title_template, "제목 틀이 없으면 general 과 다를 게 없다"


def test_거래지원_종료는_출금_기한을_반드시_묻는다():
    """출금 지원 종료 일시를 안 묻고 넘기면 회원이 자산을 뺄 기한을 모른다."""
    t = get_type("거래지원종료", "terminate")
    assert "withdraw_end_at" in t.required


def test_출금_기한이_미정인데_초안에_날짜가_있으면_오류다():
    """'미정'이라고 답했는데 초안에 기한이 적히는 경로가 실제로 있다.

    참고 공지에는 그 공지의 출금 기한이 적혀 있고, LLM 이 양식을 본뜨면서 그 날짜를 같이
    옮긴다. 이 날짜가 틀리면 회원이 기한을 잘못 알고 자산을 못 뺀다. `guards` 는 값이
    비었을 때뿐 아니라 **'미정'일 때도** 걸린다(`factcheck` 10단계).
    """
    from notice_ai.factcheck import check_draft

    part = resolve(["거래지원종료"], text="알에스에스쓰리(RSS3) 거래지원 종료", inputs={
        "coins": "알에스에스쓰리(RSS3)", "trade_end_at": "2026-10-15 15:00",
        "withdraw_end_at": "미정", "reason": "거래유의종목 지정 기간 중 소명 미흡",
    }).parts[0]
    assert part.ntype.subtype == "terminate"

    베낀_초안 = ("제목: 알에스에스쓰리(RSS3) 거래지원 종료\n본문:\n"
                "- 거래지원 종료 일시: 2026-10-15 15:00\n"
                "- 출금 지원 종료 일시: 2026-11-15 15:00\n")
    문제 = [i for i in check_draft(베낀_초안, [part]).errors if i.code == "guard"]
    assert 문제, "미정인데 초안에 출금 기한이 적혔으면 잡아야 한다"
    assert "출금 지원 종료 일시" in 문제[0].message

    안_적은_초안 = ("제목: 알에스에스쓰리(RSS3) 거래지원 종료\n본문:\n"
                   "- 거래지원 종료 일시: 2026-10-15 15:00\n"
                   "- 출금 지원 종료 시점은 확정 후 별도 공지로 안내드립니다.\n")
    assert not [i for i in check_draft(안_적은_초안, [part]).errors if i.code == "guard"]


def test_거래유의_지정과_거래지원_종료를_한_공지에_담으면_둘_다_묻는다():
    """부서 표에서 가장 흔한 두 카테고리 조합이다(#10에 적힌 49건)."""
    r = resolve(["거래유의", "거래지원종료"], text="헤미(HEMI) 거래유의종목 지정 및 거래지원 종료")
    assert [p.ntype.subtype for p in r.parts] == ["designate", "terminate"]
    물어본것 = [m["field"] for m in r.missing]
    assert "designated_at" in 물어본것 and "trade_end_at" in 물어본것
    assert 물어본것.count("coins") == 1, "두 파트가 같이 쓰는 값은 한 번만 묻는다"


# ── 입출금 중지 사유: 유형을 쪼개지 않고 고르게 한다 ─────────────────────────
def test_중지_사유는_부서_분류를_권장값으로_내놓는다():
    """부서는 중지 사유로 공지를 11개 중분류로 나눈다(919건). 우리는 유형을 쪼개지 않는다.

    쪼개면 제목 틀이 열한 개가 되고 라우팅 규칙이 서로 겹친다. 대신 사유를 고르게 해서
    같은 일을 한다.
    """
    t = get_type("입출금", "suspend")
    assert field_choices(t, "reason") == SUSPEND_REASONS
    assert "네트워크 업그레이드" in SUSPEND_REASONS
    # 입출금 유형은 그대로 둘이다. 사유로 쪼개면 아홉이 된다.
    assert [x.subtype for x in TYPES if x.category == "입출금"] == ["delay", "suspend"]


def test_권장값은_고르게만_하고_막지_않는다():
    """목록에 없는 사유를 적어도 공지를 쓸 수 있어야 한다.

    부서 표는 2026-09까지의 집계다. 새 사유가 나왔을 때 공지를 못 쓰게 되면 안 된다.
    """
    r = resolve(["입출금"], text="메가이더(MEGA) 입출금 일시 중지", inputs={
        "coins": "메가이더(MEGA)", "suspend_at": "2026-10-01 10:00",
        "reason": "거래소 내부 사정",   # 권장값에 없는 사유
    })
    assert not r.missing and not r.errors


def test_업그레이드_사유를_고르면_업그레이드_시점도_묻는다():
    """585건(입출금 중 최다)인 네트워크 업그레이드 공지는 예상 시점이 본문에 거의 항상 있다.

    사유와 무관하게 늘 묻지는 않는다 — 다른 사유에는 업그레이드 시점이란 게 없다.
    """
    t = get_type("입출금", "suspend")
    assert "upgrade_at" not in required_for(t, {"reason": "보안 이슈"})
    assert "upgrade_at" in required_for(t, {"reason": "네트워크 업그레이드"})
    assert "upgrade_at" in required_for(t, {"reason": "하드포크 진행"})


def test_조건_필드가_비어_있으면_추가로_묻지_않는다():
    """사유를 모르는 동안 업그레이드 시점을 묻는 건 순서가 뒤집힌 것이다."""
    t = get_type("입출금", "suspend")
    assert "upgrade_at" not in required_for(t, {})
    assert "upgrade_at" not in required_for(t, None)


def test_사유를_모르면_사유부터_묻고_그다음에_업그레이드_시점을_묻는다():
    r = resolve(["입출금"], text="메가이더(MEGA) 입출금 일시 중지",
                inputs={"coins": "메가이더(MEGA)", "suspend_at": "2026-10-01 10:00"})
    assert [m["field"] for m in r.missing] == ["reason"]
    assert r.missing[0]["choices"] == list(SUSPEND_REASONS), "물어볼 때 권장값을 같이 준다"

    r2 = resolve(["입출금"], text="메가이더(MEGA) 입출금 일시 중지",
                 inputs={"coins": "메가이더(MEGA)", "suspend_at": "2026-10-01 10:00",
                         "reason": "네트워크 업그레이드"})
    assert [m["field"] for m in r2.missing] == ["upgrade_at"]


# ── 카탈로그(프론트가 문답 화면을 그리는 근거) ─────────────────────────────
def test_새_카테고리도_카탈로그에_나온다():
    cats = {c["category"] for c in catalog()}
    assert {"거래지원종료", "마켓 추가", "점검"} <= cats
    assert "후기" not in cats, "공지를 쓸 일이 없는 카테고리는 넣지 않는다"


def test_카탈로그가_권장값과_조건부_필수를_같이_준다():
    """프론트가 드롭다운을 그리고, 사유를 고른 뒤 질문이 하나 더 생기는 걸 알 수 있어야 한다."""
    입출금 = next(c for c in catalog() if c["category"] == "입출금")
    suspend = next(s for s in 입출금["subtypes"] if s["subtype"] == "suspend")
    reason = next(f for f in suspend["required"] if f["field"] == "reason")
    assert reason["choices"] == list(SUSPEND_REASONS)
    assert suspend["conditional_required"] == [
        {"when_field": "reason", "when_matches": "업그레이드|하드포크", "field": "upgrade_at"}]

    마켓 = next(c for c in catalog() if c["category"] == "마켓 추가")
    market = next(f for f in 마켓["subtypes"][0]["required"] if f["field"] == "market")
    assert market["choices"] == ["원화(KRW)", "BTC", "USDT"]


@pytest.mark.parametrize("category,subtype,들어가야_할_말", [
    ("거래지원종료", "terminate", "출금 지원 종료 일시"),
    ("마켓 추가", "market_add", "마켓 추가"),
    ("점검", "external_maintenance", "재개 시점을 우리가 약속하지 않는다"),
    ("점검", "maintenance", "빗썸 전체가 멈추는 점검"),
    ("점검", "partial_maintenance", "다른 서비스는 정상"),
])
def test_새_유형의_작성_지침이_프롬프트에_실린다(category, subtype, 들어가야_할_말):
    """지침 파일은 있으면 얹고 없으면 넘어간다(`_system_prompt`). 조용히 빠져도 안 터진다.

    그래서 subtype 이름을 바꾸면 지침만 소리 없이 사라진다 — 필수 입력 검사는 그대로 도니
    테스트도 통과한다. 여기서 막는다.
    """
    from notice_ai.drafting import _system_prompt
    from notice_ai.notice_types import Part

    prompt = _system_prompt([Part(get_type(category, subtype), {})])
    assert 들어가야_할_말 in prompt


def test_GET_types_가_권장값과_조건부_필수를_실제로_내려_준다(monkeypatch):
    """프론트가 문답 화면을 그리는 통로다. `catalog()` 가 맞아도 엔드포인트에서 걸러질 수 있다.

    FastAPI 는 `response_model` 에 없는 키를 **조용히 떼어 낸다.** 나중에 응답 모델을 엄격하게
    바꾸면 드롭다운과 추가 질문이 소리 없이 사라지고, `catalog()` 테스트는 그대로 통과한다.
    """
    from fastapi.testclient import TestClient

    # 이 테스트는 인증이 아니라 /types 응답을 본다. 셸에 API_TOKEN 이 켜져 있어도(배포 테스트 등)
    # 흔들리지 않게, app 을 만들기 전에 토큰을 비운다. install 시점에 토큰을 읽으므로 import 직전에.
    monkeypatch.delenv("API_TOKEN", raising=False)
    from notice_ai.api import app

    r = TestClient(app).get("/types")
    assert r.status_code == 200
    body = r.json()
    cats = {c["category"]: c for c in (body["categories"] if isinstance(body, dict) else body)}
    assert {"거래지원종료", "마켓 추가", "점검"} <= set(cats)

    suspend = next(s for s in cats["입출금"]["subtypes"] if s["subtype"] == "suspend")
    reason = next(f for f in suspend["required"] if f["field"] == "reason")
    assert reason["choices"] == list(SUSPEND_REASONS)
    assert suspend["conditional_required"] == [
        {"when_field": "reason", "when_matches": "업그레이드|하드포크", "field": "upgrade_at"}]


def test_유형이_정의된_카테고리만_점검_대상에_넣는다():
    """TARGET_CATEGORIES 에 없으면 subtype_check·copy_check·routing_check 가 안 본다."""
    for c in ("거래지원종료", "마켓 추가", "점검"):
        assert c in TARGET_CATEGORIES
