"""코인 사전 자동 갱신 — 신규 상장을 감지해 새 인덱스로 옮긴다.

왜 필요한가. Nori 사용자 사전은 인덱스 설정에 박히고, 분석기는 색인 시점에 적용된다.
그래서 빗썸에 새 코인이 올라오면 그 이름은 사전에 없는 채로 쪼개져 색인된다('네이로'→'네이'+'로').
사전을 고치려면 인덱스를 새로 만들어 옮기는 수밖에 없다.

어떻게 하는가.
  감지  10분마다 도는 빗썸 갱신(coins)이 추가·변경을 알려 준다. 그 이름들만 지금 인덱스
        분석기에 넣어 보고(_analyze 한 번), 통째로 안 남으면 사전이 뒤처진 것이다.
  전환  새 인덱스를 지금 사전으로 만들고 → 문서를 옮기고 → 별칭을 돌린다(index_ref).
        별칭 전환 전까지 검색은 옛 인덱스가 받으므로, 중간에 실패해도 끊기지 않는다.

안전장치.
  - 옮긴 건수가 원본과 다르면 별칭을 돌리지 않는다. 반쯤 옮겨진 인덱스가 조용히 라이브가 되면
    공지가 사라진 것처럼 보인다.
  - 한 번에 하나만 돈다(락). 감지가 여러 번 떠도 겹쳐 돌지 않는다.
  - 최소 간격(DICT_REBUILD_MIN_SEC)을 둔다. 하루에 여러 종목이 상장돼도 몰아서 한 번만 돈다.
  - 옛 인덱스는 지우지 않는다. 되돌릴 곳이 남아 있어야 한다(prune으로 명시적으로만 정리).
"""

from __future__ import annotations

import logging
import threading
import time
from datetime import datetime, timezone

from notice_ai import coins, config, index_ref
from notice_ai.notice_types import now_kst
from notice_ai.opensearch_client import get_client

logger = logging.getLogger(__name__)

_lock = threading.Lock()            # 재색인은 한 번에 하나만
_state_lock = threading.Lock()
_last_finished: float = 0.0         # monotonic. 최소 간격 판정용
_state: dict = {
    "stale": [],                    # 지금 인덱스가 통째로 못 잡는 코인 이름
    "stale_since": "",
    "running": False,
    "last_rebuild": None,           # 마지막 재색인 결과(성공·실패 모두)
    "last_checked": "",
}


def state() -> dict:
    with _state_lock:
        return {**_state, "stale": list(_state["stale"]),
                "auto": config.auto_rebuild(), "min_sec": config.rebuild_min_sec()}


def _set(**kw) -> None:
    with _state_lock:
        _state.update(kw)


def _stamp() -> str:
    return now_kst().strftime("%Y-%m-%d %H:%M:%S")


# ---- 감지 ----
def unindexable(names: list[str], client=None) -> list[str]:
    """이 이름들 중 지금 인덱스 분석기가 통째로 못 잡는 것.

    토큰 수로 재지 않는다 — decompound_mode=mixed 는 원형과 조각을 둘 다 내놓으므로
    조각이 더 있어도 원형이 있으면 검색된다. '이름이 토큰 목록에 있는가'로만 본다.
    """
    from notice_ai.index_setup import _HANGUL_RE, _tokens_many

    targets = [n for n in names if n and _HANGUL_RE.search(n)]
    if not targets:
        return []
    c = client or get_client()
    toks = _tokens_many(c, targets, index=index_ref.target(c))
    return [n for n, t in zip(targets, toks) if n.lower() not in t]


def check(changed: list[str] | None = None, client=None) -> list[str]:
    """사전이 뒤처졌는지 본다. changed 를 주면 그 이름만(싸다), 없으면 전체를 본다.

    돌려주는 것은 '사전에 없어 못 잡는 이름'들. 빈 목록이면 사전이 최신이라는 뜻이다.
    """
    names = changed if changed is not None else [c.name for c in coins.known().values() if c.name]
    stale = unindexable(names, client)
    with _state_lock:
        # 전체를 본 게 아니면 이미 알던 것과 합친다. 이번에 안 본 이름이 지워지면 안 된다.
        merged = sorted(set(stale) if changed is None else set(_state["stale"]) | set(stale))
        _state["last_checked"] = _stamp()
        if merged and not _state["stale"]:
            _state["stale_since"] = _stamp()
        elif not merged:
            _state["stale_since"] = ""
        _state["stale"] = merged
        return list(merged)


# ---- 전환 ----
def _new_name() -> str:
    """새 인덱스 이름. NOTICE_INDEX 앞머리 + UTC 시각.

    시각을 붙이는 건 v3→v4 처럼 번호를 세면 이미 쓰는 번호와 부딪힐 수 있어서다.
    사전순이 곧 시간순이라 어느 게 최신인지 이름만 봐도 안다.
    """
    base = config.INDEX_NAME.rsplit("_v", 1)[0] or "notices"
    return f"{base}_{datetime.now(timezone.utc).strftime('%Y%m%d%H%M%S')}"


def rebuild(*, client=None, reason: str = "수동") -> dict:
    """지금 코인 목록으로 사전을 다시 만들어 새 인덱스로 옮기고 별칭을 돌린다.

    실패하면 별칭을 건드리지 않는다 — 옛 인덱스가 계속 검색을 받는다.
    """
    from notice_ai.index_setup import create_index, reindex_from, user_dictionary_rules

    if not _lock.acquire(blocking=False):
        return {"ok": False, "reason": "already_running", "message": "이미 재색인이 돌고 있습니다."}
    global _last_finished
    started = time.monotonic()
    out: dict = {"ok": False, "reason": "", "message": "", "source": "", "dest": "",
                 "rules": 0, "docs": 0, "copied": 0, "at": _stamp(), "trigger": reason}
    try:
        _set(running=True)
        r = coins.refresh()
        if not r["ok"]:
            out.update(reason="coins_failed", message=f"빗썸 목록을 못 받았습니다 — {r['error']}")
            return out
        rules = user_dictionary_rules()
        out["rules"] = len(rules)
        if not rules:
            # 사전 없는 인덱스로 갈아타면 지금보다 나빠진다. 그런 전환은 안 한다.
            out.update(reason="empty_dictionary", message="사전이 비어 있어 전환하지 않습니다.")
            return out

        c = client or get_client()
        src = index_ref.concrete(c)
        dest = _new_name()
        out.update(source=src, dest=dest)

        create_index(name=dest)
        moved = reindex_from(src, dest)
        out["copied"] = moved.get("created") or 0
        if moved.get("failures"):
            out.update(reason="reindex_failures",
                       message=f"옮기다 실패한 문서가 있습니다({len(moved['failures'])}건). 별칭은 그대로 둡니다.")
            return out

        c.indices.refresh(index=dest)
        before, after = c.count(index=src)["count"], c.count(index=dest)["count"]
        out["docs"] = after
        if before != after:
            # 반쯤 옮겨진 인덱스를 라이브로 올리면 공지가 사라진 것처럼 보인다. 그럴 바엔 안 바꾼다.
            #
            # 이 검사는 옮기는 도중에 들어온 색인도 같이 잡는다. reindex는 시작 시점의 문서만
            # 보므로, 도는 동안 수집이 돌면 그 공지는 새 인덱스에 없다. 그대로 별칭을 돌리면
            # 방금 들어온 공지가 사라진다. 건수가 어긋나 여기서 멈추고, 다음 기회에 다시 돈다.
            out.update(reason="count_mismatch",
                       message=f"건수가 다릅니다(원본 {before} / 새 인덱스 {after}). 별칭은 그대로 둡니다.")
            return out

        index_ref.point_at(dest, c)
        left = unindexable([x.name for x in coins.known().values() if x.name], c)
        out.update(ok=True, message=f"{dest}로 전환했습니다(문서 {after}건, 사전 {len(rules)}개).")
        with _state_lock:
            _state["stale"] = sorted(left)
            _state["stale_since"] = _stamp() if left else ""
        if left:
            out["message"] += f" 다만 아직 못 잡는 이름이 {len(left)}개 남았습니다."
        return out
    except Exception as e:
        out.update(reason=type(e).__name__, message=" ".join(str(e).split())[:300])
        logger.exception("사전 재색인 실패")
        return out
    finally:
        out["took_sec"] = round(time.monotonic() - started, 1)
        _last_finished = time.monotonic()
        _set(running=False, last_rebuild=out)
        _lock.release()


def _cooled_down() -> bool:
    return not _last_finished or (time.monotonic() - _last_finished) >= config.rebuild_min_sec()


def on_coins_changed(result: dict) -> None:
    """coins 갱신 루프가 매번 부른다. 바뀐 게 없으면 아무것도 안 한다."""
    if not result.get("ok"):
        return
    touched = sorted(set(result.get("added") or []) | set(result.get("changed") or []))
    if not touched:
        return
    names = [c.name for t in touched if (c := coins.get(t)) and c.name]
    try:
        stale = check(names)
    except Exception as e:
        logger.warning("사전 확인 실패(%s) — 다음 갱신 때 다시 봅니다.", type(e).__name__)
        return
    if not stale:
        return
    logger.info("사전에 없는 코인 %d개: %s", len(stale), ", ".join(stale[:10]))
    if not config.auto_rebuild():
        logger.info("DICT_AUTO_REBUILD=0 — 재색인은 직접 돌려 주세요.")
        return
    if not _cooled_down():
        logger.info("최소 간격(%.0f초) 안이라 이번에는 건너뜁니다.", config.rebuild_min_sec())
        return
    rebuild(reason=f"신규/변경 {len(touched)}종")
