"""general 비율 진단 — 유형을 늘린 효과를 재는 자리 (#10).

가짜 OpenSearch 로 돈다. 접속하지 않는다.

이 진단이 하는 일은 하나다: 카테고리마다 제목이 유형에 걸리는지 세고, **안 걸린 제목을
그대로 보여 준다.** 그 제목들이 다음에 만들 유형의 후보다. 숫자만 주면 다음 작업을 못 한다.
"""

from __future__ import annotations

import pytest

from notice_ai import routing_check

# 전부 부서 분류표의 실제 대표 공지 제목이다
문서 = [
    ("알에스에스쓰리(RSS3) 거래지원 종료", ["거래지원종료"]),
    ("셀피 체인(SLF) 거래지원 종료 (출금 지원 종료 일시 변경)", ["거래지원종료"]),
    ("2026년 상반기 결산 안내", ["거래지원종료"]),          # 유형에 안 걸린다
    ("빗썸 서비스 점검 안내 (완료)", ["점검"]),
    ("거래 서비스 점검 안내 (완료)", ["점검"]),
    ("정부24 점검으로 인한 주민등록증 진위확인 서비스 일시 중단 안내", ["점검"]),
    ("하반기 운영 계획", ["점검"]),                          # 유형에 안 걸린다
    ("텔러파이낸스(DEBIT) 원화 마켓 추가", ["마켓 추가"]),
    # 한 공지가 카테고리 둘에 걸리는 경우(부서 표에서 가장 흔한 조합)
    ("헤미(HEMI) 거래유의종목 지정 및 거래지원 종료", ["거래유의", "거래지원종료"]),
    ("", ["점검"]),                                          # 제목 없는 문서는 세지 않는다
]


class Boom(Exception):
    """검색 서버가 잠깐 안 되는 상황."""


@pytest.fixture
def 가짜색인(monkeypatch):
    """`_titles` 를 바꿔 끼운다 — helpers.scan 까지 흉내 낼 이유가 없다."""
    def _titles(client, index, categories):
        for title, cats in 문서:
            if not categories or any(c in categories for c in cats):
                yield title, cats

    monkeypatch.setattr(routing_check, "_titles", _titles)
    monkeypatch.setattr(routing_check, "get_client", lambda: object())
    monkeypatch.setattr(routing_check.index_ref, "target", lambda c=None: "notices_live")


def _cat(out: dict, name: str) -> dict:
    return next(c for c in out["categories"] if c["category"] == name)


def test_유형에_걸린_것과_안_걸린_것을_갈라_센다(가짜색인):
    out = routing_check.diagnose("점검")
    점검 = _cat(out, "점검")
    assert 점검["total"] == 4          # 제목 없는 문서는 빠졌다
    assert 점검["general"] == 1
    assert 점검["subtypes"] == {"partial_maintenance": 1, "external_maintenance": 1,
                                "maintenance": 1}
    assert 점검["general_ratio"] == 0.25


def test_general로_간_제목을_그대로_보여_준다(가짜색인):
    """다음에 만들 유형이 거기 있다. 숫자만 주면 다음 작업을 못 한다."""
    out = routing_check.diagnose("점검")
    assert _cat(out, "점검")["general_titles"] == ["하반기 운영 계획"]


def test_카테고리_둘에_걸린_공지는_양쪽에서_센다(가짜색인):
    """'A 거래유의종목 지정 및 B 거래지원 종료'는 두 카테고리의 공지다."""
    out = routing_check.diagnose()
    assert _cat(out, "거래유의")["subtypes"] == {"designate": 1}
    assert _cat(out, "거래지원종료")["subtypes"]["terminate"] == 3


def test_비우면_유형이_정의된_카테고리를_전부_본다(가짜색인):
    out = routing_check.diagnose()
    assert [c["category"] for c in out["categories"]] == list(routing_check.TARGET_CATEGORIES)
    # 문서가 없는 카테고리도 0으로 나온다 — 빠지면 '안 봤다'와 구분이 안 된다
    공시 = _cat(out, "공시")
    assert 공시["total"] == 0 and 공시["general_ratio"] == 0.0


def test_유형이_없는_카테고리를_물으면_왜_안_되는지_알려_준다(가짜색인):
    out = routing_check.diagnose("후기")
    assert "유형이 정의되어 있지 않습니다" in out["error"]
    assert out["categories"] == []


def test_접속이_안_되면_모양은_그대로고_error만_찬다(monkeypatch):
    """부르는 쪽(엔드포인트·CLI)이 터지지 않아야 한다."""
    def boom():
        raise Boom("접속 불가")

    monkeypatch.setattr(routing_check, "get_client", boom)
    out = routing_check.diagnose()
    assert out["error"] and out["categories"] == [] and out["checked"] == 0
    assert set(out) == {"error", "index", "checked", "categories"}


def test_훑는_중_터져도_모양은_그대로다(가짜색인, monkeypatch):
    def boom(client, index, categories):
        yield "빗썸 서비스 점검 안내", ["점검"]
        raise Boom("스크롤 끊김")

    monkeypatch.setattr(routing_check, "_titles", boom)
    out = routing_check.diagnose("점검")
    assert "Boom" in out["error"] and out["categories"] == []


def test_본문을_받지_않는다(monkeypatch):
    """제목만 보는 진단이다. 본문·임베딩까지 받으면 훑는 비용이 몇 배가 된다."""
    import sys
    import types as pytypes

    물어본것: list[dict] = []

    def scan(client, index, size, preserve_order, query):
        물어본것.append(query)
        return iter(())

    fake = pytypes.ModuleType("opensearchpy")
    fake.helpers = pytypes.SimpleNamespace(scan=scan)
    monkeypatch.setitem(sys.modules, "opensearchpy", fake)

    list(routing_check._titles(object(), "notices_live", ["점검"]))
    assert 물어본것[0]["_source"] == ["title", "categories"]
    assert 물어본것[0]["query"] == {"terms": {"categories": ["점검"]}}


# ── 주소 환경변수 ────────────────────────────────────────────────────────
@pytest.mark.parametrize("given,기대", [
    # AWS 콘솔이 보여 주는 꼴. 스킴이 없다
    ("search-x.ap-northeast-2.es.amazonaws.com", "https://search-x.ap-northeast-2.es.amazonaws.com"),
    ("https://search-x.es.amazonaws.com/", "https://search-x.es.amazonaws.com"),
    ("  search-x.es.amazonaws.com  ", "https://search-x.es.amazonaws.com"),
    ("http://localhost:9200", "http://localhost:9200"),
])
def test_주소에_스킴이_없으면_https를_붙인다(monkeypatch, given, 기대):
    """스킴 없는 주소를 그냥 넘기면 한참 아래 opensearchpy 안에서 TypeError 로 터진다.

    `urlparse('search-x...').hostname` 이 None 이라 `if ":" in host` 에서 죽는데, 주소가
    문제라는 말이 아무 데도 안 나온다. '서버가 막혔나' 쪽을 뒤지게 된다 — 실제로 그랬다.
    """
    from notice_ai import config

    monkeypatch.setenv("OPENSEARCH_ENDPOINT", given)
    assert config.endpoint() == 기대


@pytest.mark.parametrize("given", ["", "   ", "https://", "https:///path"])
def test_주소를_못_읽으면_뜰_때_알려_준다(monkeypatch, given):
    from notice_ai import config

    monkeypatch.setenv("OPENSEARCH_ENDPOINT", given)
    with pytest.raises(config.ConfigError):
        config.endpoint()
