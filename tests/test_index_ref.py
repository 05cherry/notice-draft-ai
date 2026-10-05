"""별칭 층 — 사전 자동 갱신이 사람 손 없이 이어지게 만든 자리(#34).

가짜 OpenSearch 로 돈다. 접속하지 않는다.

여기서 지키는 핵심은 두 가지다.
  1. 별칭 전환이 **한 번의 호출**로 끝난다(사이에 빈 순간이 없다).
  2. 별칭을 인덱스로 착각하지 않는다 — `indices.exists` 는 별칭에도 200을 준다.
"""

from __future__ import annotations

import pytest

from notice_ai import config, index_ref

ALIAS = "notices_live"
OLD = "notices_20260101000000"
NEW = "notices_20260202000000"


class Boom(Exception):
    """검색 서버가 잠깐 안 되는 상황."""


class FakeIndices:
    """인덱스 이름 → 붙은 별칭 집합."""

    def __init__(self, indices: dict[str, set[str]] | None = None):
        self.indices = {k: set(v) for k, v in (indices or {}).items()}
        self.update_calls: list[list[dict]] = []
        self.fail = False

    def _check(self):
        if self.fail:
            raise Boom("접속 불가")

    def _for_alias(self, alias: str) -> list[str]:
        return sorted(i for i, al in self.indices.items() if alias in al)

    def exists_alias(self, name: str) -> bool:
        self._check()
        return bool(self._for_alias(name))

    def get_alias(self, name: str) -> dict:
        self._check()
        found = self._for_alias(name)
        if not found:
            raise Boom(f"별칭 없음: {name}")
        return {i: {"aliases": {name: {}}} for i in found}

    def get(self, index: str) -> dict:
        """진짜 인덱스면 그 이름을, 별칭이면 가리키는 진짜 이름을 열쇠로 준다."""
        self._check()
        if index in self.indices:
            return {index: {"settings": {}}}
        if found := self._for_alias(index):
            return {i: {"settings": {}} for i in found}
        raise Boom(f"없음: {index}")

    def update_aliases(self, body: dict) -> dict:
        self._check()
        actions = body["actions"]
        self.update_calls.append(actions)
        for a in actions:
            (op, spec), = a.items()
            if op == "add":
                self.indices.setdefault(spec["index"], set()).add(spec["alias"])
            elif op == "remove":
                self.indices.get(spec["index"], set()).discard(spec["alias"])
        return {"acknowledged": True}


class FakeClient:
    def __init__(self, indices: dict[str, set[str]] | None = None):
        self.indices = FakeIndices(indices)


@pytest.fixture(autouse=True)
def 별칭_설정(monkeypatch):
    monkeypatch.setattr(config, "INDEX_ALIAS", ALIAS)
    monkeypatch.setattr(config, "INDEX_NAME", "notices_v3")
    index_ref.forget()
    yield
    index_ref.forget()


# ── 별칭인가 인덱스인가 ──────────────────────────────────────────────────
def test_진짜_인덱스는_진짜라고_말한다():
    client = FakeClient({OLD: {ALIAS}})
    assert index_ref.is_real_index(OLD, client) is True


def test_별칭은_인덱스가_아니라고_말한다():
    """#47 — 여기서 True 가 나오면 두 번째 전환부터 '이미 인덱스로 있습니다'로 막힌다.

    `indices.exists`(HEAD /이름)로는 가릴 수 없다. 그 API 는 별칭에도 200 을 준다.
    """
    client = FakeClient({OLD: {ALIAS}})
    assert index_ref.is_real_index(ALIAS, client) is False


def test_없는_이름은_인덱스가_아니다():
    assert index_ref.is_real_index("notices_없음", FakeClient()) is False


# ── 전환 ────────────────────────────────────────────────────────────────
def test_별칭이_없으면_새로_만든다():
    client = FakeClient({NEW: set()})
    out = index_ref.point_at(NEW, client)
    assert out == {"alias": ALIAS, "before": [], "now": NEW}
    assert client.indices.indices[NEW] == {ALIAS}


def test_전환은_호출_한_번으로_끝난다():
    """remove 와 add 가 따로 나가면 그 사이에 별칭이 아무것도 안 가리키는 순간이 생긴다."""
    client = FakeClient({OLD: {ALIAS}, NEW: set()})
    index_ref.point_at(NEW, client)

    assert len(client.indices.update_calls) == 1, "전환이 여러 번에 나뉘었다"
    actions = client.indices.update_calls[0]
    assert {"remove": {"index": OLD, "alias": ALIAS}} in actions
    assert {"add": {"index": NEW, "alias": ALIAS}} in actions
    assert client.indices.indices[OLD] == set()
    assert client.indices.indices[NEW] == {ALIAS}


def test_이미_그걸_가리키면_아무것도_안_바꾼다():
    client = FakeClient({NEW: {ALIAS}})
    out = index_ref.point_at(NEW, client)
    assert client.indices.update_calls == []
    assert out["before"] == [NEW] and out["now"] == NEW


def test_별칭_이름이_인덱스로_있으면_거부한다():
    """사용자가 실제로 만난 경우. 별칭과 인덱스는 이름을 겹칠 수 없다."""
    client = FakeClient({ALIAS: set()})
    with pytest.raises(RuntimeError, match="이미 인덱스로 있습니다"):
        index_ref.point_at(NEW, client)


def test_별칭과_같은_이름으로는_못_돌린다():
    with pytest.raises(ValueError):
        index_ref.point_at(ALIAS, FakeClient())


def test_별칭을_안_쓰면_전환도_없다(monkeypatch):
    monkeypatch.setattr(config, "INDEX_ALIAS", "")
    with pytest.raises(RuntimeError, match="NOTICE_ALIAS"):
        index_ref.point_at(NEW, FakeClient())


# ── 진짜 이름 ───────────────────────────────────────────────────────────
def test_진짜_이름을_돌려준다():
    """만들기·지우기·재색인은 별칭이 아니라 이 이름으로 해야 한다."""
    assert index_ref.concrete(FakeClient({OLD: {ALIAS}})) == OLD


def test_별칭이_없으면_NOTICE_INDEX가_진짜_이름이다():
    assert index_ref.concrete(FakeClient()) == "notices_v3"


def test_별칭이_여럿을_가리키면_사람을_부른다():
    """우리 쪽에서 이렇게 만들지 않는다. 어느 쪽이 원본인지 코드가 정할 일이 아니다."""
    client = FakeClient({OLD: {ALIAS}, NEW: {ALIAS}})
    with pytest.raises(RuntimeError, match="여럿"):
        index_ref.concrete(client)


# ── 검색·색인이 보는 이름 ────────────────────────────────────────────────
def test_별칭이_있으면_별칭을_쓴다():
    assert index_ref.target(FakeClient({OLD: {ALIAS}})) == ALIAS


def test_별칭이_없으면_NOTICE_INDEX를_쓴다():
    assert index_ref.target(FakeClient()) == "notices_v3"


def test_접속이_안_되면_직전_답을_지킨다(monkeypatch):
    """여기서 NOTICE_INDEX 로 떨어지면, 이미 옮겨 간 뒤에 옛 인덱스를 조용히 읽는다."""
    client = FakeClient({OLD: {ALIAS}})
    assert index_ref.target(client) == ALIAS

    monkeypatch.setattr(index_ref, "_at", 0.0)   # 기억은 두고 TTL 만 만료시킨다
    client.indices.fail = True
    assert index_ref.target(client) == ALIAS, "접속 실패에 옛 인덱스로 떨어졌다"


def test_기억을_버린_직후_접속이_안_되면_NOTICE_INDEX로_간다():
    """지금 그렇게 돈다는 기록. 직전 답이 없으면 지킬 것도 없기 때문이다.

    `point_at` 이 전환 끝에 `forget()` 을 부르므로, 전환 직후 별칭 조회만 실패하고
    검색은 되는 좁은 구간에서는 옛 인덱스를 읽을 수 있다. 검색까지 안 되는 상황이면
    어차피 아무것도 못 읽으니 지금은 그냥 두고, 이 검사로 눈에 보이게만 해 둔다.
    """
    client = FakeClient({OLD: {ALIAS}})
    index_ref.forget()
    client.indices.fail = True
    assert index_ref.target(client) == "notices_v3"


def test_답을_기억해_두고_다시_안_묻는다():
    client = FakeClient({OLD: {ALIAS}})
    assert index_ref.target(client) == ALIAS
    client.indices.indices = {}                 # 별칭이 사라져도
    assert index_ref.target(client) == ALIAS    # 기억한 답을 쓴다(TTL 안)

    index_ref.forget()
    assert index_ref.target(client) == "notices_v3"
