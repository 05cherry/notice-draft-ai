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

    def get_alias(self, name: str = "", index: str = "") -> dict:
        self._check()
        if index:                       # 패턴으로 물으면 인덱스별 별칭 전부를 준다
            return {i: {"aliases": {a: {} for a in sorted(al)}}
                    for i, al in self.indices.items()}
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


class FakeCat:
    """`cat.indices` 흉내. 만든 시각(epoch ms)은 넘겨받은 순서대로 준다."""

    def __init__(self, owner: "FakeIndices", created: dict[str, int] | None = None):
        self.owner = owner
        self.created = created or {}

    def indices(self, index: str = "*", format: str = "json", h: str = "") -> list[dict]:
        rows = []
        for i, name in enumerate(sorted(self.owner.indices)):
            at = self.created.get(name, 1_700_000_000_000 + i)
            rows.append({
                "index": name,
                "docs.count": "100",
                "store.size": "1mb",
                "creation.date": str(at),
                "creation.date.string": str(at),
                "health": "green",
            })
        return rows


class FakeClient:
    def __init__(self, indices: dict[str, set[str]] | None = None,
                 created: dict[str, int] | None = None):
        self.indices = FakeIndices(indices)
        self.cat = FakeCat(self.indices, created)


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


# ── 남아 있는 인덱스 훑기 ────────────────────────────────────────────────
OLDER = "notices_20251201000000"
ORPHAN = "notices_20260303000000"      # 재색인이 중간에 끊겨 남은 것(가장 나중에 만들어졌다)


def 훑기(client) -> dict[str, str]:
    return {d["name"]: d["keep"] for d in index_ref.inventory(client=client)}


def test_쓰는_것과_되돌릴_곳만_지키고_나머지는_후보로_둔다():
    client = FakeClient({
        "notices": set(), OLDER: set(), NEW: {ALIAS},
    }, created={"notices": 1, OLDER: 2, NEW: 3})
    keep = 훑기(client)

    assert "가리키는 중" in keep[NEW]
    assert "되돌릴 곳" in keep[OLDER]
    assert keep["notices"] == "", "오래된 것까지 붙잡고 있으면 정리할 수 없다"


def test_NOTICE_INDEX는_별칭이_가리키지_않아도_지킨다():
    """별칭이 사라지면 코드가 돌아갈 이름이다."""
    client = FakeClient({"notices_v3": set(), NEW: {ALIAS}},
                        created={"notices_v3": 1, NEW: 2})
    assert "NOTICE_INDEX" in 훑기(client)["notices_v3"]


def test_재색인이_끊겨_남은_더_새_인덱스는_되돌릴_곳이_아니다():
    """지금 쓰는 것보다 **나중에** 만들어진 것은 세대가 앞이 아니라 찌꺼기다.

    이름으로 줄 세우면 이 구분이 안 된다. 만든 시각으로 세는 이유.
    """
    client = FakeClient({OLDER: set(), NEW: {ALIAS}, ORPHAN: set()},
                        created={OLDER: 1, NEW: 2, ORPHAN: 3})
    keep = 훑기(client)

    assert keep[ORPHAN] == "", "끊긴 재색인 찌꺼기를 되돌릴 곳으로 붙잡았다"
    assert "되돌릴 곳" in keep[OLDER]


def test_이름이_아니라_만든_시각으로_줄_세운다():
    """이름의 시각이 실제 만든 시각과 다를 수 있다. 줄 세우는 기준은 이름이 아니다.

    아래에서 이름으로 세우면 'notices_20250101…' 이 맨 아래로 가지만, 실제로는 가장
    나중에 만들어졌다(끊긴 재색인 찌꺼기). 맨 앞에 와야 한다.
    """
    MISNAMED = "notices_20250101000000"
    client = FakeClient(
        {MISNAMED: set(), NEW: {ALIAS}, OLDER: set()},
        created={OLDER: 1, NEW: 2, MISNAMED: 9},
    )
    assert [d["name"] for d in index_ref.inventory(client=client)] == [MISNAMED, NEW, OLDER]


def test_다른_별칭이_붙어_있으면_지키고_이유를_적는다():
    client = FakeClient({OLDER: {"notices_backup"}, NEW: {ALIAS}},
                        created={OLDER: 1, NEW: 2})
    assert "notices_backup" in 훑기(client)[OLDER]


def test_시스템_인덱스는_세지_않는다():
    client = FakeClient({".kibana": set(), NEW: {ALIAS}}, created={".kibana": 1, NEW: 2})
    assert ".kibana" not in 훑기(client)


def test_건수와_용량을_같이_준다():
    rows = index_ref.inventory(client=FakeClient({NEW: {ALIAS}}))
    assert rows[0]["docs"] == 100 and rows[0]["size"] == "1mb"
    assert rows[0]["aliases"] == [ALIAS]
    assert "_at" not in rows[0], "내부 정렬 열쇠가 응답에 샜다"


def test_목록을_못_읽으면_왜인지_말한다():
    client = FakeClient({NEW: {ALIAS}})
    client.indices.fail = True
    client.cat.indices = lambda **kw: (_ for _ in ()).throw(Boom("접속 불가"))
    with pytest.raises(RuntimeError, match="읽지 못했습니다"):
        index_ref.inventory(client=client)


def test_별칭이_여럿을_가리키면_아무것도_지워도_된다고_하지_않는다():
    """비정상 상태다. 어느 쪽이 원본인지 모르는 채로 지울 후보를 내놓으면 안 된다."""
    client = FakeClient({OLDER: {ALIAS}, NEW: {ALIAS}}, created={OLDER: 1, NEW: 2})
    keep = 훑기(client)
    assert all(keep.values()), f"지워도 된다고 말한 것이 있다: {keep}"
    assert "여럿" in keep[OLDER] and "여럿" in keep[NEW]


def test_만든_시각을_못_읽은_인덱스는_후보로_내놓지_않는다():
    """세대를 셀 수 없는 채로 '지워도 된다'고 하면 되돌릴 곳을 지우게 할 수 있다."""
    client = FakeClient({OLDER: set(), NEW: {ALIAS}, "notices_??": set()},
                        created={OLDER: 1, NEW: 2, "notices_??": 0})
    keep = 훑기(client)
    assert "손으로 확인" in keep["notices_??"]
