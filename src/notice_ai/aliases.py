"""코인 별칭 확장 — 시드 JSON + 제목에서 티커/이름 추출.

'샌드박스(SAND)' 표기에서 티커를 뽑아 색인 시 tickers 필드에 넣고,
검색 시엔 별칭을 쿼리에 붙여 리콜을 높인다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

_ALIAS_PATH = Path(__file__).resolve().parents[2] / "data" / "coin_aliases.json"
_NAME_TICKER_RE = re.compile(r"([가-힣A-Za-z0-9 .]+?)\s*\(([A-Z0-9]{2,10})\)")


@lru_cache(maxsize=1)
def _seed() -> dict[str, list[str]]:
    if not _ALIAS_PATH.exists():
        return {}
    return json.loads(_ALIAS_PATH.read_text(encoding="utf-8"))


def tickers_in(text: str) -> list[str]:
    """제목 등에서 (TICKER) 패턴의 티커만 추출."""
    return sorted({t for _, t in _NAME_TICKER_RE.findall(text)})


def extract_aliases_from_titles(titles: list[str]) -> dict[str, set[str]]:
    out: dict[str, set[str]] = {}
    for t in titles:
        for name, ticker in _NAME_TICKER_RE.findall(t):
            name = name.strip()
            if name:
                out.setdefault(ticker, set()).add(name)
    return out


def expand(ticker: str | None, name: str | None = None) -> list[str]:
    terms: set[str] = set()
    if ticker:
        terms.add(ticker)
        terms.update(_seed().get(ticker.upper(), []))
    if name:
        terms.add(name)
    return sorted(terms)
