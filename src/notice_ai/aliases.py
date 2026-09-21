"""코인 별칭 확장 — 시드 JSON + 제목에서 티커/이름 추출.

'샌드박스(SAND)' 표기에서 티커를 뽑아 색인 시 tickers 필드에 넣고,
검색 시엔 별칭을 쿼리에 붙여 리콜을 높인다.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path

from notice_ai import coins

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
    """검색어에 붙일 별칭들.

    시드 JSON(data/coin_aliases.json)은 손으로 채운 것이라 신규 상장을 모른다. 그래서 빗썸
    거래 대상 캐시에 있으면 거기 한글명·영문명도 같이 넣는다(coins 참고). 리콜만 늘리는 추가라
    캐시가 비어 있어도 전과 똑같이 동작한다.
    """
    terms: set[str] = set()
    if ticker:
        terms.add(ticker)
        terms.update(_seed().get(ticker.upper(), []))
    if name:
        terms.add(name)
    if found := coins.get(ticker or name):
        terms.update(t for t in (found.ticker, found.name, found.english) if t)
    return sorted(terms)
