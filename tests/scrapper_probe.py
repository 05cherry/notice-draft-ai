"""상세 본문 경로 확인 — 실제 데이터 있는 카테고리(입출금=7) 사용.

알아낸 사실:
  - category는 '전체' 없음. 카테고리별 ID로 조회. (0은 빈 결과)
  - pageProps에 totalCount 있음 → 카테고리별 총건수 파악 가능.
남은 확인: 상세 공지의 '본문(raw_text)'을 어떻게 꺼내는가.
"""

import json
import re
import time

import cloudscraper

BASE = "https://feed.bithumb.com"
CATEGORY = 7   # 입출금 (데이터 많음)
GAP = 3.0
scraper = cloudscraper.create_scraper(browser="chrome")


def get(url):
    r = scraper.get(url)
    if r.status_code == 429:
        raise SystemExit(f"429: {url}\n→ 5~10분 쉬었다가 다시.")
    return r


def dump(label, obj, n=3500):
    print(f"\n===== {label} =====")
    print(json.dumps(obj, ensure_ascii=False, indent=2)[:n])


def main():
    html = get(f"{BASE}/notice").text
    build_id = re.search(r'"buildId":"(.*?)"', html).group(1)
    print("buildId:", build_id)
    time.sleep(GAP)

    r = get(f"{BASE}/_next/data/{build_id}/notice.json?category={CATEGORY}&page=1")
    print("[목록] STATUS:", r.status_code)
    pp = r.json().get("pageProps", {})
    notices = pp.get("noticeList", [])
    print("[목록] 건수:", len(notices), " totalCount:", pp.get("totalCount"))
    if not notices:
        raise SystemExit("이 카테고리도 비어있음 — 다른 category로 시도 필요")
    sample_id = notices[0]["id"]
    print("[목록] 샘플 id:", sample_id, " 제목:", notices[0].get("title"))
    time.sleep(GAP)

    # 상세 경로 1: _next/data
    durl = f"{BASE}/_next/data/{build_id}/notice/{sample_id}.json?id={sample_id}"
    r = get(durl)
    print("\n[상세 JSON] STATUS:", r.status_code)
    if r.status_code == 200:
        dpp = r.json().get("pageProps", {})
        print("[상세 JSON] pageProps 키들:", list(dpp.keys()))
        dump("[상세 JSON] 내용", dpp)
        return

    # 상세 경로 2: 상세 HTML의 __NEXT_DATA__
    time.sleep(GAP)
    r = get(f"{BASE}/notice/{sample_id}")
    print("\n[상세 HTML] STATUS:", r.status_code, "LEN:", len(r.text))
    m = re.search(r'<script id="__NEXT_DATA__"[^>]*>(.*?)</script>', r.text, re.S)
    if m:
        dpp = json.loads(m.group(1)).get("props", {}).get("pageProps", {})
        print("[상세 HTML] pageProps 키들:", list(dpp.keys()))
        dump("[상세 HTML] 내용", dpp)
    else:
        print("[상세 HTML] __NEXT_DATA__ 못 찾음:", r.text[:600])


if __name__ == "__main__":
    main()