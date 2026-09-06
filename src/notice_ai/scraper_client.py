"""빗썸 공지 스크래핑 클라이언트 (cloudscraper + Next.js _next/data).

공식 공지 API가 최신 20건·본문 미제공이라, 전체 이력+본문을 위해
사이트의 Next.js 데이터 엔드포인트를 사용한다.

구조(진단으로 확인):
  buildId  : /notice HTML의 "buildId":"..."
  목록     : /_next/data/{buildId}/notice.json?category={id}&page={n}
             → pageProps.noticeList (페이지당 30), pageProps.totalCount
  상세     : /_next/data/{buildId}/notice/{id}.json?id={id}
             → pageProps.data.content (HTML, 이스케이프됨)

주의: 빗썸이 요청 속도에 민감(429). 요청 사이 간격을 넉넉히 둔다.
buildId는 재배포 시 바뀌므로 매 실행마다 새로 얻는다.
"""

from __future__ import annotations

import html
import json
import re
import time

import cloudscraper

BASE = "https://feed.bithumb.com"

# data/categories.json 과 동일. 코드에서도 바로 쓰도록 내장.
CATEGORY_IDS = {
    "거래유의": 5, "거래지원종료": 6, "공시": 15, "마켓 추가": 9,
    "수수료 이벤트": 16, "신규서비스": 2, "안내": 1, "업데이트": 4,
    "이벤트": 8, "입출금": 7, "점검": 3, "후기": 17,
}

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\n{3,}")


class RateLimited(Exception):
    """429. 호출측에서 대기 후 재시도 판단."""


class BithumbScraper:
    def __init__(self, gap_sec: float = 2.0):
        self._s = cloudscraper.create_scraper(browser="chrome")
        self._gap = gap_sec
        self._last = 0.0
        self._build_id: str | None = None

    # ---- 내부 ----
    def _throttle(self) -> None:
        wait = self._gap - (time.time() - self._last)
        if wait > 0:
            time.sleep(wait)
        self._last = time.time()

    def _get(self, url: str):
        self._throttle()
        r = self._s.get(url)
        if r.status_code == 429:
            raise RateLimited(url)
        return r

    def build_id(self, refresh: bool = False) -> str:
        if self._build_id and not refresh:
            return self._build_id
        r = self._get(f"{BASE}/notice")
        m = re.search(r'"buildId":"(.*?)"', r.text)
        if not m:
            raise RuntimeError("buildId 추출 실패 (차단 가능성)")
        self._build_id = m.group(1)
        return self._build_id

    # ---- 공개 API ----
    def list_page(self, category_id: int, page: int) -> tuple[list[dict], int]:
        """(noticeList, totalCount) 반환."""
        bid = self.build_id()
        url = f"{BASE}/_next/data/{bid}/notice.json?category={category_id}&page={page}"
        r = self._get(url)
        if r.status_code == 404:
            # buildId 만료 가능 → 한 번 갱신 후 재시도
            bid = self.build_id(refresh=True)
            url = f"{BASE}/_next/data/{bid}/notice.json?category={category_id}&page={page}"
            r = self._get(url)
        r.raise_for_status()
        pp = r.json().get("pageProps", {})
        return pp.get("noticeList", []), pp.get("totalCount", 0)

    def detail(self, notice_id: int) -> dict:
        """상세 data dict 반환 (content 포함)."""
        bid = self.build_id()
        url = f"{BASE}/_next/data/{bid}/notice/{notice_id}.json?id={notice_id}"
        r = self._get(url)
        if r.status_code == 404:
            bid = self.build_id(refresh=True)
            url = f"{BASE}/_next/data/{bid}/notice/{notice_id}.json?id={notice_id}"
            r = self._get(url)
        r.raise_for_status()
        return r.json().get("pageProps", {}).get("data", {})


def html_to_text(raw: str) -> str:
    """이스케이프된 HTML content → 평문.

    content는 이중 이스케이프 상태(&lt;p&gt;...)라 unescape 후 태그 제거.
    """
    if not raw:
        return ""
    unescaped = html.unescape(raw)          # &lt; → < , &nbsp; → 공백 등
    text = unescaped.replace("<br>", "\n").replace("<br/>", "\n").replace("<br />", "\n")
    text = re.sub(r"</(p|li|ul|div)>", "\n", text)
    text = _TAG_RE.sub("", text)            # 남은 태그 제거
    text = html.unescape(text)              # 혹시 남은 엔티티
    text = _WS_RE.sub("\n\n", text)
    return text.strip()
