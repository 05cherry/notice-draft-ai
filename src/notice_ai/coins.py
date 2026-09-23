"""빗썸 거래 대상 목록 — 코인 한글명·티커를 수기 입력 대신 자동으로 채운다.

지금까지는 '메가이더(MEGA)'처럼 사람이 직접 적어 넣었다. 오타가 나거나 신규 상장 코인을
모르면 그대로 공지에 실린다. 여기서 빗썸 공개 API로 거래 대상 목록을 받아 두고,
티커만 적으면 한글명을, 한글명만 적으면 티커를 채워 준다.

저장은 **프로세스 메모리 한 벌**이 전부다. 10분(COINS_REFRESH_SEC)마다 백그라운드로 받아
통째로 갈아 끼운다. 그래서 이렇다:
  - 서버가 재시작하면 목록도 사라진다(뜨자마자 한 번 받으므로 몇 초면 다시 찬다).
  - 인스턴스가 여러 개면 각자 자기 것을 갖는다. 목록이 자주 안 바뀌니 문제되지 않는다.
  - 빗썸이 죽어 있으면 **갖고 있던 목록을 그대로 쓴다**. 못 받았다고 목록을 비우지 않는다.

차후 OpenSearch를 도커로 내리면서 PostgreSQL을 붙일 때는 `MemoryCoinStore`만 같은 모양의
DB 구현으로 갈아 끼우면 된다. 조회는 전부 store를 거치고, 갱신은 store.replace() 한 번이다.

환경변수(전부 선택. 기본값으로 그냥 동작한다):
    COINS_REFRESH_SEC   갱신 주기(초). 기본 600(10분). 0이면 자동 갱신을 끈다.
    COINS_TIMEOUT_SEC   빗썸 호출 제한시간(초). 기본 10.
    BITHUMB_MARKET_URL  거래 대상 목록 주소. 기본 https://api.bithumb.com/v1/market/all
"""

from __future__ import annotations

import asyncio
import logging
import os
import re
import threading
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx

logger = logging.getLogger(__name__)

_DEFAULT_URL = "https://api.bithumb.com/v1/market/all"

# 'KRW-BTC' → 기준통화 KRW + 티커 BTC. 빗썸 v1은 업비트와 같은 이 표기를 쓴다.
_MARKET_RE = re.compile(r"^([A-Z]{2,5})-([A-Z0-9]{2,20})$")
_TICKER_RE = re.compile(r"^[A-Z0-9]{2,20}$")
_NORM_RE = re.compile(r"[\s.\-_()]+")


def market_url() -> str:
    return os.environ.get("BITHUMB_MARKET_URL", _DEFAULT_URL).strip() or _DEFAULT_URL


def refresh_sec() -> float:
    return _float_env("COINS_REFRESH_SEC", 600.0)


def timeout_sec() -> float:
    return _float_env("COINS_TIMEOUT_SEC", 10.0) or 10.0


def _float_env(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, "") or default)
    except ValueError:
        logger.warning("%s 값을 숫자로 못 읽어 기본값 %s를 씁니다.", name, default)
        return default


@dataclass(frozen=True, slots=True)
class Coin:
    """거래 대상 하나. ticker는 대문자, name은 빗썸이 주는 한글명."""

    ticker: str
    name: str = ""
    english: str = ""
    markets: tuple[str, ...] = ()       # 붙어 있는 마켓. 예) ('BTC', 'KRW')
    warning: bool = False               # 빗썸이 유의 표시를 달아 둔 종목

    @property
    def label(self) -> str:
        """공지에 쓰는 표기. 예) '이더리움(ETH)'"""
        return f"{self.name}({self.ticker})" if self.name else self.ticker

    def as_dict(self) -> dict:
        return {"ticker": self.ticker, "name": self.name, "english": self.english,
                "markets": list(self.markets), "warning": self.warning}


def _norm(value) -> str:
    """'The Sandbox', 'the-sandbox', '샌드박스 ' 를 같은 열쇠로 만든다."""
    return _NORM_RE.sub("", str(value or "")).lower()


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ---- 빗썸 응답 읽기 ----
def _rows(payload) -> list[dict]:
    """응답에서 목록만 꺼낸다.

    v1(`/v1/market/all`)은 배열을 그대로 준다. 구 공개 API는 {"status": "0000", "data": ...}로
    한 겹 싸서 주므로 둘 다 받아 둔다 — 주소를 바꿔 끼워도 터지지 않게.
    """
    if isinstance(payload, dict):
        payload = payload.get("data") or payload.get("markets") or []
    if not isinstance(payload, list):
        return []
    return [r for r in payload if isinstance(r, dict)]


def _merge(old: Coin, ticker: str, name: str, english: str, base: str, warning: bool) -> Coin:
    """같은 티커가 여러 마켓(KRW-ETH, BTC-ETH)으로 오면 한 줄로 합친다."""
    markets = set(old.markets) | ({base} if base else set())
    return Coin(ticker, old.name or name, old.english or english,
                tuple(sorted(markets)), old.warning or warning)


def parse(rows) -> dict[str, Coin]:
    """빗썸 응답 → {티커: Coin}. 읽을 수 없는 줄은 조용히 버린다."""
    out: dict[str, Coin] = {}
    for r in _rows(rows):
        market = str(r.get("market") or r.get("symbol") or "").strip().upper()
        if m := _MARKET_RE.match(market):
            base, ticker = m.group(1), m.group(2)
        elif _TICKER_RE.match(market):
            base, ticker = "", market       # 'BTC'처럼 마켓 없이 티커만 주는 응답도 받아 준다
        else:
            continue                        # 그 밖의 값은 코인이 아니다. 목록에 섞으면 안 된다
        name = str(r.get("korean_name") or r.get("korean") or "").strip()
        english = str(r.get("english_name") or r.get("english") or "").strip()
        # isDetails=true 일 때만 오는 값. 'NONE'이 아니면 유의 표시가 달린 것.
        warning = str(r.get("market_warning") or "NONE").strip().upper() not in ("", "NONE")
        old = out.get(ticker)
        out[ticker] = _merge(old, ticker, name, english, base, warning) if old else Coin(
            ticker, name, english, (base,) if base else (), warning)
    return out


def fetch(*, url: str = "", timeout: float = 0.0) -> dict[str, Coin]:
    """빗썸에서 거래 대상 목록을 받아 온다. 실패하면 예외를 그대로 올린다(refresh가 받는다)."""
    url = url or market_url()
    # User-Agent를 비워 두면 막는 곳이 있다(빗썸 웹은 그래서 cloudscraper를 쓴다). API는 이걸로 충분하다.
    res = httpx.get(url, params={"isDetails": "true"}, timeout=timeout or timeout_sec(),
                    headers={"Accept": "application/json", "User-Agent": "notice-draft-ai/0.1"},
                    follow_redirects=True)
    res.raise_for_status()
    coins = parse(res.json())
    if not coins:
        # 빈 목록을 캐시에 넣으면 이름 채우기가 조용히 멈춘다. 실패로 친다.
        raise ValueError(f"빗썸 응답에서 거래 대상을 한 건도 못 읽었습니다(url={url}).")
    return coins


# ---- 저장소 ----
class MemoryCoinStore:
    """프로세스 메모리 한 벌. PostgreSQL로 옮길 때 이 클래스만 갈아 끼운다.

    통째로 바꿔 끼우기만 하고 안에서 고치지 않는다(교체는 lock, 조회는 그냥 읽기).
    조회하는 쪽이 받아 간 dict는 다음 갱신이 와도 그대로 남아 있어 중간에 바뀌지 않는다.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._coins: dict[str, Coin] = {}
        self._index: dict[str, str] = {}    # 티커·한글명·영문명(정규화) → 티커
        self._updated_at = ""
        self._error = ""

    def replace(self, coins: dict[str, Coin]) -> None:
        index: dict[str, str] = {}
        for c in coins.values():                       # 티커를 먼저 넣어 이름과 겹쳐도 티커가 이긴다
            index.setdefault(_norm(c.ticker), c.ticker)
        for c in coins.values():
            for key in (_norm(c.name), _norm(c.english)):
                if key:
                    index.setdefault(key, c.ticker)
        with self._lock:
            self._coins, self._index, self._updated_at, self._error = coins, index, _now(), ""

    def fail(self, error: str) -> None:
        """갱신에 실패했다고만 적어 둔다. 갖고 있던 목록은 그대로 둔다."""
        with self._lock:
            self._error = error

    def all(self) -> dict[str, Coin]:
        with self._lock:
            return self._coins

    def lookup(self, key) -> Coin | None:
        with self._lock:
            ticker = self._index.get(_norm(key))
            return self._coins.get(ticker) if ticker else None

    def meta(self) -> dict:
        with self._lock:
            return {"count": len(self._coins), "updated_at": self._updated_at, "error": self._error}


_store: MemoryCoinStore = MemoryCoinStore()


def store() -> MemoryCoinStore:
    return _store


# ---- 갱신 ----
def _diff(before: dict[str, Coin], after: dict[str, Coin]) -> dict[str, list[str]]:
    changed = [t for t in before.keys() & after.keys() if before[t] != after[t]]
    return {"added": sorted(after.keys() - before.keys()),
            "removed": sorted(before.keys() - after.keys()),
            "changed": sorted(changed)}


def refresh(*, target: MemoryCoinStore | None = None) -> dict:
    """빗썸에서 받아 캐시를 통째로 갈아 끼운다.

    실패해도 예외를 올리지 않는다 — 10분마다 도는 루프가 한 번 실패했다고 서버가 흔들리면 안 되고,
    갖고 있던 목록으로 계속 버티는 편이 낫다. 결과는 돌려주는 dict의 ok로 본다.
    """
    target = target or _store
    before = target.all()
    try:
        coins = fetch()
    except Exception as e:
        msg = f"{type(e).__name__}: {' '.join(str(e).split())[:160]}"
        target.fail(msg)
        logger.warning("빗썸 거래 대상 갱신 실패 — 갖고 있던 %d건을 계속 씁니다. %s", len(before), msg)
        return {"ok": False, "count": len(before), "error": msg}

    d = _diff(before, coins)
    target.replace(coins)
    if not before:
        logger.info("빗썸 거래 대상 %d건을 처음 받았습니다.", len(coins))
    elif any(d.values()):
        logger.info("빗썸 거래 대상 %d건 — 추가 %s / 제외 %s / 변경 %s",
                    len(coins), d["added"] or "없음", d["removed"] or "없음", d["changed"] or "없음")
    return {"ok": True, "count": len(coins), **d}


# ---- 백그라운드 루프(FastAPI lifespan이 켜고 끈다) ----
_task: asyncio.Task | None = None
_listeners: list = []


def on_change(fn) -> None:
    """갱신이 끝날 때마다 부를 함수를 등록한다(refresh 결과 dict를 받는다).

    코인 목록이 인덱스 사전보다 앞서 나가는지 보는 쪽(dictionary)이 여기에 붙는다.
    coins가 인덱스를 알 필요는 없으므로 방향을 이렇게 둔다.
    """
    if fn not in _listeners:
        _listeners.append(fn)


def _refresh_and_notify() -> dict:
    """스레드에서 돈다. 등록된 쪽이 오래 걸려도(재색인) 이벤트 루프는 안 막힌다."""
    r = refresh()
    for fn in list(_listeners):
        try:
            fn(r)
        except Exception:
            logger.exception("코인 갱신 후처리 실패 — 목록 갱신 자체는 계속합니다.")
    return r


async def _loop(interval: float) -> None:
    while True:
        # refresh는 동기(httpx)라서 이벤트 루프를 막지 않게 스레드로 넘긴다.
        await asyncio.to_thread(_refresh_and_notify)
        await asyncio.sleep(interval)


def start() -> None:
    """뜨자마자 한 번 받고, 이후 COINS_REFRESH_SEC마다 갱신한다. 0이면 끈다."""
    global _task
    interval = refresh_sec()
    if interval <= 0:
        logger.info("COINS_REFRESH_SEC=0 — 빗썸 코인 목록 자동 갱신을 끕니다.")
        return
    if _task and not _task.done():
        return
    _task = asyncio.get_running_loop().create_task(_loop(interval), name="bithumb-coins")
    logger.info("빗썸 코인 목록 자동 갱신 시작(%.0f초마다).", interval)


async def stop() -> None:
    """루프를 멈춘다.

    취소는 다음 주기를 막을 뿐, 이미 스레드에서 돌고 있는 호출까지 끊지는 못한다
    (asyncio.to_thread는 스레드를 취소할 수 없다). 그 한 건은 끝나고 캐시에 써도
    서버가 내려가는 중이라 해가 없다.
    """
    global _task
    task, _task = _task, None
    if not task or task.done():
        return
    task.cancel()
    try:
        await task
    except asyncio.CancelledError:
        pass


# ---- 조회 ----
def known() -> dict[str, Coin]:
    """지금 갖고 있는 전체 목록 {티커: Coin}."""
    return _store.all()


def get(key) -> Coin | None:
    """티커·한글명·영문명 아무거나로 찾는다. 대소문자·공백·하이픈은 무시한다."""
    return _store.lookup(key) if key else None


def search(q: str = "", limit: int = 500) -> list[Coin]:
    """자동완성용. q가 비면 전체를 티커 순으로 준다.

    정확히 일치 > 앞부분 일치 > 포함 순으로 정렬하고, 같은 순위면 짧게 맞은 쪽을 앞에 둔다.
    그래야 '이더'가 이더리움(ETH)을 이더리움클래식(ETC)보다 앞에 놓는다.
    """
    coins = sorted(known().values(), key=lambda c: c.ticker)
    needle = _norm(q)
    if not needle:
        return coins[:limit]
    ranked = []
    for c in coins:
        if hit := _match((_norm(c.ticker), _norm(c.name), _norm(c.english)), needle):
            ranked.append((*hit, c.ticker, c))
    ranked.sort(key=lambda r: r[:3])
    return [r[-1] for r in ranked[:limit]]


def _match(keys, needle: str) -> tuple[int, int] | None:
    """(순위, 맞은 열쇠 길이) 중 가장 좋은 것. 어디에도 안 맞으면 None."""
    best = None
    for k in keys:
        if not k:
            continue
        if k == needle:
            rank = 0
        elif k.startswith(needle):
            rank = 1
        elif needle in k:
            rank = 2
        else:
            continue
        if best is None or (rank, len(k)) < best:
            best = (rank, len(k))
    return best


def fill(coins: list[dict]) -> list[dict]:
    """[{'name','ticker'}]에서 **빈 쪽만** 캐시로 채운다.

    사람이 적어 넣은 값은 건드리지 않는다. 빗썸과 다른 표기를 일부러 쓰는 공지가 있고,
    맞다고 단정할 수 있는 건 '비어 있다'는 사실뿐이라서다. 캐시가 비었으면 그대로 돌려준다.
    """
    out = []
    for c in coins:
        name = str(c.get("name") or "").strip()
        ticker = str(c.get("ticker") or "").strip().upper()
        if not (name and ticker) and (found := get(ticker or name)):
            name, ticker = name or found.name, ticker or found.ticker
        out.append({**c, "name": name, "ticker": ticker})
    return out


def status() -> dict:
    """/health?deep=true 에 실을 상태.

    ok는 셋으로 나뉜다 — True(목록 있음) / False(한 번도 못 받음) / None(아직 안 받아 봄).
    방금 뜬 서버가 첫 갱신 전이라고 degraded로 떨어지면 안 되니 None을 따로 둔다.
    """
    m = _store.meta()
    out: dict = {"ok": True if m["count"] else (False if m["error"] else None),
                 "count": m["count"], "updated_at": m["updated_at"] or None,
                 "refresh_sec": refresh_sec(), "source": market_url()}
    if m["error"]:
        out["error"] = m["error"]
        out["stale"] = bool(m["count"])     # 목록은 있는데 마지막 갱신이 실패 = 오래된 값
    return out
