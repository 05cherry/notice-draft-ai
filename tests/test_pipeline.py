"""백엔드 무관 순수 로직 테스트 — OpenSearch/Bedrock/네트워크 불필요."""

from notice_ai.aliases import extract_aliases_from_titles, tickers_in
from notice_ai.assembly import assemble
from notice_ai.fusion import rrf_fuse
from notice_ai.ingest import row_to_doc



def test_tickers_in() -> None:
    assert tickers_in("샌드박스(SAND) 유의촉구 안내") == ["SAND"]


def test_extract_aliases() -> None:
    m = extract_aliases_from_titles(["샌드박스(SAND) 안내", "The Sandbox (SAND) 중단"])
    assert "샌드박스" in m["SAND"] and "The Sandbox" in m["SAND"]


def test_assemble_deposit() -> None:
    aq = assemble(
        {"coin_kr": "샌드박스", "ticker": "SAND", "action": "입출금 일시 중지", "reason": "지갑 점검"},
        "deposit-withdrawal", category_name="입출금",
    )
    assert "샌드박스(SAND)" in aq.query_text
    assert aq.filters == {"category": "입출금", "ticker": "SAND"}


def test_csv_row_to_doc_flexible_headers() -> None:
    row = {"제목": "넥소(NEXO) 원화 마켓 추가", "본문": "안녕하세요...", "카테고리": "마켓추가",
           "url": "https://feed.bithumb.com/notice/999", "게시일": "2025-01-01"}
    d = row_to_doc(row)
    assert d["title"].startswith("넥소")
    assert d["raw_text"] == "안녕하세요..."
    assert d["categories"] == ["마켓추가"]
    assert d["tickers"] == ["NEXO"]
    assert d["source_url"].endswith("/999")


def test_csv_row_without_url_gets_stable_id() -> None:
    d = row_to_doc({"title": "본문만 있는 공지", "body": "내용"})
    assert d["source_url"].startswith("csv:")


def test_rrf_consensus() -> None:
    assert rrf_fuse([["a", "c", "e"], ["c", "b", "a"]])[0][0] == "c"


# ── 스크래퍼: HTML 본문 평문화 ──────────────────────────────
def test_html_to_text_unescapes_and_strips():
    from notice_ai.scraper_client import html_to_text

    raw = (
        "&lt;p&gt;&lt;span&gt;안녕하세요.&lt;br&gt;빗썸입니다.&lt;/span&gt;&lt;/p&gt;"
        "&lt;ul&gt;&lt;li&gt;대상 가상자산&lt;br&gt;- 제타체인(ZETA)&amp;nbsp;&lt;/li&gt;"
        "&lt;li&gt;입출금 중지 시점&lt;br&gt;- 2026.08.25(화) 오후 11시 20분&lt;/li&gt;&lt;/ul&gt;"
    )
    text = html_to_text(raw)
    assert "<" not in text and ">" not in text        # 태그 전부 제거
    assert "안녕하세요." in text
    assert "제타체인(ZETA)" in text
    assert "2026.08.25" in text
    assert "&lt;" not in text and "&nbsp;" not in text  # 엔티티 정리


def test_list_item_doc_maps_fields():
    from notice_ai.collector import _list_item_doc

    item = {
        "id": 1654654,
        "categoryName1": "입출금",
        "categoryName2": None,
        "title": "제타체인(ZETA) 입출금 일시 중단 안내",
        "publicationDateTime": "2026-08-25 23:19:20",
        "modifyDateTime": "2026-08-25 23:19:20",
    }
    d = _list_item_doc(item)
    assert d["source_url"].endswith("/notice/1654654")
    assert d["external_id"] == 1654654
    assert d["categories"] == ["입출금"]
    assert d["tickers"] == ["ZETA"]


def test_list_item_doc_multi_category():
    """거래유의 + 거래지원종료 → 두 카테고리 모두 categories에 들어가야."""
    from notice_ai.collector import _list_item_doc

    item = {
        "id": 1640868,
        "categoryName1": "거래유의",
        "categoryName2": "거래지원종료",
        "title": "거래유의종목 및 거래지원 종료 일정 안내",
        "publicationDateTime": "2025-12-01 10:00:00",
        "modifyDateTime": "2025-12-01 10:00:00",
    }
    d = _list_item_doc(item)
    assert d["categories"] == ["거래유의", "거래지원종료"]
