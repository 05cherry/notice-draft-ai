"""빗썸 공지 수집 → OpenSearch 색인 (cloudscraper + _next/data).

두 모드:
  collect_category(name)  : 특정 카테고리 전체 페이지 순회
  collect_all()           : 모든 카테고리 순회

각 공지마다:
  1) 목록에서 id/제목/카테고리/날짜 수집
  2) 상세(detail)에서 본문(content) 받아 평문화 → raw_text
  3) OpenSearch 색인 (dedup: source_url = _id)

속도: 빗썸이 429에 민감. 기본 gap 2초. 429 만나면 대기 후 재시도.
재실행 안전: 이미 색인된 공지(source_url 존재)는 상세 요청도 건너뜀.
"""

from __future__ import annotations

import logging
import time

from notice_ai import aliases, config
from notice_ai.opensearch_client import get_client
from notice_ai.scraper_client import (
    CATEGORY_IDS,
    BithumbScraper,
    RateLimited,
    html_to_text,
)

logger = logging.getLogger(__name__)

BASE = "https://feed.bithumb.com"
PAGE_SIZE = 30  # 관찰된 페이지당 건수


def _source_url(notice_id: int) -> str:
    return f"{BASE}/notice/{notice_id}"


def _list_item_doc(item: dict) -> dict:
    """목록 항목 → 문서 뼈대(본문 제외).

    categoryName1/2를 하나의 categories 배열로 합친다.
    → 한 공지가 여러 카테고리에 동시에 걸릴 수 있음(예: 거래유의+거래지원종료).
    """
    title = item.get("title", "") or ""
    c1 = item.get("categoryName1")
    c2 = item.get("categoryName2")
    categories = [c for c in (c1, c2) if c]
    return {
        "source_url": _source_url(item["id"]),
        "external_id": item["id"],
        "categories": categories,          # 배열: ["거래유의","거래지원종료"] 또는 ["입출금"]
        "tickers": aliases.tickers_in(title),
        "title": title,
        "published_at": item.get("publicationDateTime") or None,
        "modified_at": item.get("modifyDateTime") or None,
    }


def _retry_on_429(fn, *, tries: int = 5, wait: float = 60.0):
    for attempt in range(tries):
        try:
            return fn()
        except RateLimited:
            if attempt == tries - 1:
                raise
            logger.warning("429 — %.0f초 대기 후 재시도(%d/%d)", wait, attempt + 1, tries)
            time.sleep(wait)


def collect_category(
    name: str,
    scraper: BithumbScraper | None = None,
    max_pages: int | None = None,
    fetch_body: bool = True,
) -> int:
    """한 카테고리 전체 순회 색인. 반환값: 신규 색인 건수."""
    if name not in CATEGORY_IDS:
        raise ValueError(f"알 수 없는 카테고리: {name}. 가능: {list(CATEGORY_IDS)}")
    cat_id = CATEGORY_IDS[name]
    scraper = scraper or BithumbScraper()
    os_client = get_client()
    saved = 0
    page = 1
    total = None
    while True:
        notices, total_count = _retry_on_429(lambda: scraper.list_page(cat_id, page))
        if total is None:
            total = total_count
            logger.info("[%s] 총 %d건", name, total)
        if not notices:
            break
        for item in notices:
            doc = _list_item_doc(item)
            url = doc["source_url"]
            if os_client.exists(index=config.INDEX_NAME, id=url):
                continue  # 이미 있음 → 상세 요청도 생략
            if fetch_body:
                data = _retry_on_429(lambda i=item: scraper.detail(i["id"]))
                doc["raw_text"] = html_to_text(data.get("content", ""))
            else:
                doc["raw_text"] = ""
            os_client.index(index=config.INDEX_NAME, id=url, body=doc)
            saved += 1
            logger.info("색인 [%s] %s", name, doc["title"])
        if max_pages and page >= max_pages:
            break
        if total and page * PAGE_SIZE >= total:
            break
        page += 1
    logger.info("[%s] 신규 %d건", name, saved)
    return saved


def collect_all(
    scraper: BithumbScraper | None = None,
    max_pages: int | None = None,
    fetch_body: bool = True,
) -> int:
    scraper = scraper or BithumbScraper()
    grand = 0
    for name in CATEGORY_IDS:
        grand += collect_category(name, scraper, max_pages=max_pages, fetch_body=fetch_body)
    logger.info("전체 신규 %d건", grand)
    return grand
