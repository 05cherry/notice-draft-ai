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


def point_at(index: str, client=None) -> dict:
    """별칭을 이 인덱스로 돌린다(원자적). 별칭이 없으면 새로 만든다."""
    c = client or _client()
    alias = config.INDEX_ALIAS
    if not alias:
        raise RuntimeError("NOTICE_ALIAS가 비어 있어 별칭을 쓸 수 없습니다.")
    if alias == index:
        raise ValueError(f"별칭과 인덱스 이름이 같습니다({alias}). NOTICE_ALIAS를 다른 이름으로 두세요.")
    if c.indices.exists(index=alias):
        raise RuntimeError(f"'{alias}'가 이미 인덱스로 있습니다. 별칭은 인덱스와 이름이 겹칠 수 없습니다.")

    before = sorted(c.indices.get_alias(name=alias)) if c.indices.exists_alias(name=alias) else []
    actions = [{"remove": {"index": i, "alias": alias}} for i in before if i != index]
    if index not in before:
        actions.append({"add": {"index": index, "alias": alias}})
    if actions:
        c.indices.update_aliases(body={"actions": actions})   # 한 번에 적용 = 사이에 빈 순간이 없다
    forget()
    return {"alias": alias, "before": before, "now": index}
