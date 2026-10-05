"""검색·색인이 실제로 쓸 인덱스 이름을 한 곳에서 정한다.

사전(Nori user_dictionary_rules)은 색인 시점에 박힌다. 그래서 새 코인이 상장되면 인덱스를
새로 만들어 옮기는 수밖에 없다. 그때마다 NOTICE_INDEX를 손으로 고쳐야 한다면 자동 갱신은
불가능하다 — 마지막 한 걸음이 사람 손이라서.

그래서 별칭(alias)을 하나 두고 코드는 그것만 본다. 새 인덱스로 옮길 때 별칭만 돌리면 되고,
그 전환은 OpenSearch에서 원자적이라 검색이 한 건도 안 끊긴다. 옮기다 실패하면 별칭이 그대로
옛 인덱스를 가리키므로 저절로 복구된다.

별칭이 아직 없으면 NOTICE_INDEX를 그대로 쓴다. 별칭을 만들기 전에도 전과 똑같이 돈다.
"""

from __future__ import annotations

import logging
import threading
import time

from notice_ai import config

logger = logging.getLogger(__name__)

_TTL = 60.0     # 별칭 존재 여부를 이 초만큼 기억한다(매 검색마다 물어볼 것은 아니라서)
_lock = threading.Lock()
_at: float = 0.0
_name: str = ""


def _client():
    from notice_ai.opensearch_client import get_client

    return get_client()


def target(client=None) -> str:
    """검색·색인이 쓸 이름. 별칭이 있으면 별칭, 없으면 NOTICE_INDEX."""
    global _at, _name
    with _lock:
        if _name and time.monotonic() - _at < _TTL:
            return _name
        last = _name

    name = _resolve(client, last)
    with _lock:
        _at, _name = time.monotonic(), name
    return name


def _resolve(client, last: str) -> str:
    alias = config.INDEX_ALIAS
    if not alias:
        return config.INDEX_NAME
    try:
        if (client or _client()).indices.exists_alias(name=alias):
            return alias
    except Exception as e:
        # 검색 서버가 잠깐 안 될 뿐일 수 있다. 전에 별칭을 봤다면 그 답을 유지한다 —
        # 여기서 NOTICE_INDEX로 떨어지면 이미 옮겨 간 뒤에 옛 인덱스를 조용히 읽게 된다.
        logger.debug("별칭 확인 실패(%s) — 직전 답(%s)을 씁니다.", type(e).__name__, last or "없음")
        return last or config.INDEX_NAME
    return config.INDEX_NAME


def forget() -> None:
    """기억한 답을 버린다. 별칭을 만들거나 돌린 직후에 부른다."""
    global _at, _name
    with _lock:
        _at, _name = 0.0, ""


def concrete(client=None) -> str:
    """별칭이 가리키는 진짜 인덱스 이름. 만들기·지우기·재색인은 이 이름으로 해야 한다.

    별칭이 없으면 NOTICE_INDEX가 곧 진짜 이름이다.
    """
    c = client or _client()
    alias = config.INDEX_ALIAS
    if alias:
        try:
            got = c.indices.get_alias(name=alias)
        except Exception:
            got = {}
        if names := sorted(got):
            if len(names) > 1:
                # 우리 쪽에서 이렇게 만들지 않는다. 손으로 붙였거나 전환이 중간에 끊긴 것이라
                # 어느 쪽을 원본으로 삼을지 사람이 정해야 한다.
                raise RuntimeError(f"별칭 '{alias}'가 인덱스 여럿({', '.join(names)})을 가리킵니다.")
            return names[0]
    return config.INDEX_NAME


def is_real_index(name: str, client=None) -> bool:
    """이 이름이 '진짜 인덱스'인가 — 별칭이면 False.

    indices.exists(HEAD /이름)로는 가릴 수 없다. 그 API는 별칭도 200을 주기 때문에
    별칭을 한 번 만들고 나면 '이미 인덱스로 있다'고 잘못 말하게 된다.
    indices.get 은 별칭으로 물어도 가리키는 진짜 인덱스 이름을 열쇠로 돌려주므로,
    물어본 이름이 열쇠에 그대로 있으면 그것이 진짜 인덱스다.
    """
    c = client or _client()
    try:
        return name in (c.indices.get(index=name) or {})
    except Exception:
        return False                                  # 없으면 없는 것


def point_at(index: str, client=None) -> dict:
    """별칭을 이 인덱스로 돌린다(원자적). 별칭이 없으면 새로 만든다.

    여러 번 불러도 안전하다. 이미 그 인덱스를 가리키고 있으면 아무것도 바꾸지 않는다.
    """
    c = client or _client()
    alias = config.INDEX_ALIAS
    if not alias:
        raise RuntimeError("NOTICE_ALIAS가 비어 있어 별칭을 쓸 수 없습니다.")
    if alias == index:
        raise ValueError(f"별칭과 인덱스 이름이 같습니다({alias}). NOTICE_ALIAS를 다른 이름으로 두세요.")
    if is_real_index(alias, c):
        raise RuntimeError(f"'{alias}'가 이미 인덱스로 있습니다. 별칭은 인덱스와 이름이 겹칠 수 없습니다. "
                           f"NOTICE_ALIAS를 다른 이름으로 두거나 그 인덱스를 정리하세요.")

    before = sorted(c.indices.get_alias(name=alias)) if c.indices.exists_alias(name=alias) else []
    actions = [{"remove": {"index": i, "alias": alias}} for i in before if i != index]
    if index not in before:
        actions.append({"add": {"index": index, "alias": alias}})
    if actions:
        c.indices.update_aliases(body={"actions": actions})   # 한 번에 적용 = 사이에 빈 순간이 없다
    forget()
    return {"alias": alias, "before": before, "now": index}


# ── 어떤 인덱스가 남아 있는가 ─────────────────────────────────────────────
#
# 재색인할 때마다 인덱스가 하나씩 쌓이고 자동으로 지우지 않는다(되돌릴 곳이 있어야 하므로).
# 그래서 가끔 사람이 정리해야 하는데, 지금 뭐가 있고 어느 것이 쓰이는지 볼 방법이 없었다.
# 여기 있는 것은 **읽기만** 한다. 지우는 길은 일부러 만들지 않는다 — 인덱스 삭제는 되돌릴
# 수 없고, 공유 토큰 하나로 열어 두기엔 위험하다(#6에서 다룰 부분).
KEEP_ROLLBACK = 1       # 지금 쓰는 것 말고 되돌릴 곳으로 남겨 둘 개수


def inventory(pattern: str = "*", client=None) -> list[dict]:
    """인덱스 목록 + 각각을 왜 두는지/지워도 되는지. 아무것도 바꾸지 않는다.

    `keep` 이 빈 문자열인 것만 지워도 되는 후보다. 판단 기준은 셋이다.
      - 별칭이 가리키는 것: 지금 검색·색인이 쓰는 곳. 절대 안 된다
      - NOTICE_INDEX: 별칭이 없거나 사라졌을 때 돌아갈 이름
      - 지금 쓰는 것보다 바로 먼저 만들어진 것 하나: 재색인이 잘못됐을 때 별칭을 되돌릴 곳

    만든 시각으로 줄 세운다. 이름으로 세우면 안 된다 — 'notices_v3' 와
    'notices_20260928221926' 을 글자로 비교하면 'v' 가 숫자보다 커서 세대가 뒤집힌다.
    """
    c = client or _client()
    alias = config.INDEX_ALIAS

    try:
        rows = c.cat.indices(index=pattern, format="json",
                             h="index,docs.count,store.size,creation.date,creation.date.string,health")
    except Exception as e:
        raise RuntimeError(f"인덱스 목록을 읽지 못했습니다: {type(e).__name__}") from e

    try:
        aliased = c.indices.get_alias(index=pattern) or {}
    except Exception:
        aliased = {}

    def aliases_of(name: str) -> list[str]:
        return sorted((aliased.get(name, {}).get("aliases") or {}))

    live = ""
    if alias:
        pointed = [i for i in aliased if alias in (aliased[i].get("aliases") or {})]
        live = pointed[0] if len(pointed) == 1 else ""

    out = []
    for r in rows:
        name = r.get("index", "")
        if name.startswith("."):
            continue                                  # 시스템 인덱스는 우리 것이 아니다
        out.append({
            "name": name,
            "docs": int(r.get("docs.count") or 0),
            "size": r.get("store.size") or "",
            "created": r.get("creation.date.string") or "",
            "health": r.get("health") or "",
            "aliases": aliases_of(name),
            "keep": "",
            "_at": int(r.get("creation.date") or 0),
        })

    out.sort(key=lambda d: d["_at"], reverse=True)     # 최근에 만든 것이 앞

    # 되돌릴 곳은 '지금 쓰는 것보다 먼저 만들어진 것' 중에서 센다. 재색인이 중간에 끊겨
    # 지금 쓰는 것보다 나중에 만들어진 인덱스가 남아 있을 수 있는데, 그건 되돌릴 곳이 아니다.
    in_use = live or config.INDEX_NAME
    names = [d["name"] for d in out]
    after = names.index(in_use) + 1 if in_use in names else 0

    rollback = 0
    for i, d in enumerate(out):
        if d["name"] == live:
            d["keep"] = f"별칭 '{alias}'가 가리키는 중 — 지우면 검색이 멈춥니다"
        elif d["name"] == config.INDEX_NAME:
            d["keep"] = "NOTICE_INDEX — 별칭이 없을 때 돌아갈 이름"
        elif d["aliases"]:
            d["keep"] = f"다른 별칭이 붙어 있음({', '.join(d['aliases'])})"
        elif i >= after and rollback < KEEP_ROLLBACK:
            d["keep"] = "되돌릴 곳으로 남겨 둠(바로 앞 세대)"
            rollback += 1

    for d in out:
        d.pop("_at")
    return out
