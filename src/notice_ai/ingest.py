"""사내 공지 데이터(CSV) 색인 — 전체 이력 + 본문 채우기.

공지 API는 최신 20건·본문 없음이라, 전체 이력과 본문은 사내에서 받은
CSV로 채운다. 이 경로가 요구사항 2(본문 기반 초안 생성)를 실제로 가능하게 한다.

CSV 컬럼(헤더 이름은 유연하게 매핑):
    필수: title, raw_text
    권장: source_url(또는 url), category(또는 categories), published_at
컬럼명이 달라도 아래 _COLMAP의 후보로 최대한 맞춘다.
source_url이 없으면 title 해시로 안정적 _id를 만든다(중복 방지).
"""

from __future__ import annotations

import csv
import hashlib
import logging

from notice_ai import aliases, config
from notice_ai.opensearch_client import get_client

logger = logging.getLogger(__name__)

# 표준 필드 → 허용하는 CSV 헤더 후보들
_COLMAP = {
    "title": ["title", "제목", "subject"],
    "raw_text": ["raw_text", "body", "content", "본문", "내용"],
    "source_url": ["source_url", "url", "pc_url", "링크", "주소"],
    "category": ["category", "categories", "카테고리", "구분"],
    "published_at": ["published_at", "date", "게시일", "등록일", "작성일"],
}


def _pick(row: dict, field: str) -> str:
    for cand in _COLMAP[field]:
        for key in row:
            if key and key.strip().lower() == cand.lower():
                return (row[key] or "").strip()
    return ""


def _doc_id(source_url: str, title: str) -> str:
    if source_url:
        return source_url
    return "csv:" + hashlib.sha1(title.encode("utf-8")).hexdigest()[:16]


def row_to_doc(row: dict) -> dict:
    title = _pick(row, "title")
    cats_raw = _pick(row, "category")
    cats = [c.strip() for c in cats_raw.replace("/", ",").split(",") if c.strip()]
    url = _pick(row, "source_url")
    return {
        "source_url": url or _doc_id("", title),
        "categories": cats,                 # 배열로 통일
        "tickers": aliases.tickers_in(title),
        "title": title,
        "raw_text": _pick(row, "raw_text"),
        "published_at": _pick(row, "published_at") or None,
    }


def ingest_csv(path: str) -> int:
    client = get_client()
    saved = 0
    with open(path, encoding="utf-8-sig", newline="") as f:
        reader = csv.DictReader(f)
        for row in reader:
            doc = row_to_doc(row)
            if not doc["title"]:
                continue
            _id = _doc_id(doc["source_url"], doc["title"])
            client.index(index=config.INDEX_NAME, id=_id, body=doc)
            saved += 1
            if saved % 200 == 0:
                logger.info("CSV 색인 %d건", saved)
    client.indices.refresh(index=config.INDEX_NAME)
    return saved
