"""쌓인 인덱스 치우기(#53) — 되돌릴 수 없는 일이라 '안 지우는 쪽'을 주로 본다.

재색인이 한 번 돌 때마다 인덱스가 하나 쌓인다(실측 약 104mb). 치우는 쪽이 없으면 1년에
1~3GB가 되고, 디스크가 차면 자동 갱신이 조용히 실패한다 — 검색은 계속 되는데 새로 상장된
코인만 안 잡히는 상태가 되므로 원인을 찾기 어렵다.

그래서 치우기는 넣되, **지워선 안 될 것을 지우지 않는다**는 쪽에 검사를 몰아 둔다.
가짜 OpenSearch로 돈다. 접속하지 않는다.
"""

from __future__ import annotations

import time

import pytest

from notice_ai import config, dictionary, index_ref

from test_index_ref import ALIAS, Boom, FakeClient

LIVE = "notices_20261002094607"      # 사용자의 실제 상태에서 가져온 이름들
PREV = "notices_20260928221926"
OLD1 = "notices_20260901000000"
OLD2 = "notices_20260801000000"

HOUR = 3600_000
NOW = int(time.time() * 1000)


@pytest.fixture(autouse=True)
def 설정(monkeypatch):
    monkeypatch.setattr(config, "INDEX_ALIAS", ALIAS)
    monkeypatch.setattr(config, "INDEX_NAME", "notices_v4")
    monkeypatch.setenv("DICT_PRUNE", "1")
    index_ref.forget()
    yield
    index_ref.forget()


def 쌓인것(**extra) -> FakeClient:
    """지금 쓰는 것 + 되돌릴 곳 + 오래된 자동 생성 둘."""
    indices = {LIVE: {ALIAS}, PREV: set(), OLD1: set(), OLD2: set()}
    created = {LIVE: NOW - 3 * 24 * HOUR, PREV: NOW - 7 * 24 * HOUR,
               OLD1: NOW - 34 * 24 * HOUR, OLD2: NOW - 65 * 24 * HOUR}
    indices.update({k: v[0] for k, v in extra.items()})
    created.update({k: v[1] for k, v in extra.items()})
    return FakeClient(indices, created=created)


def 후보(client) -> list[str]:
    take, _ = index_ref.prunable(client)
    return [d["name"] for d in take]


def 건너뜀(client) -> dict[str, str]:
    _, skip = index_ref.prunable(client)
    return {d["name"]: d["skip"] for d in skip}


# ── 무엇을 후보로 고르는가 ────────────────────────────────────────────────
def test_오래된_자동_생성_인덱스만_고른다():
    assert 후보(쌓인것()) == [OLD1, OLD2]


def test_지금_쓰는_것과_되돌릴_곳은_후보가_아니다():
    names = 후보(쌓인것())
    assert LIVE not in names and PREV not in names


def test_손으로_만든_이름은_안_건드린다():
    """`notices_v4`·`notices`·남이 올린 샘플 데이터. 이름 꼴에서 걸러져야 한다."""
    client = 쌓인것(**{
        "notices": (set(), NOW - 200 * 24 * HOUR),
        "notices_v2": (set(), NOW - 190 * 24 * HOUR),
        "opensearch_dashboards_sample_data_ecommerce": (set(), NOW - 300 * 24 * HOUR),
    })
    names, skipped = 후보(client), 건너뜀(client)

    for manual in ("notices", "notices_v2", "opensearch_dashboards_sample_data_ecommerce"):
        assert manual not in names, f"{manual} 를 지우려 했다"
        assert "자동 생성된 이름이 아닙니다" in skipped[manual]
    assert names == [OLD1, OLD2]


def test_NOTICE_INDEX는_이름에서도_판정에서도_걸러진다():
    """`notices_v4` 는 keep 이 붙어 후보에 안 오고, 설령 와도 이름 꼴이 안 맞는다."""
    client = 쌓인것()
    client.indices.indices["notices_v4"] = set()
    client.cat.created["notices_v4"] = NOW - 100 * 24 * HOUR
    assert "notices_v4" not in 후보(client)


def test_갓_만든_것은_안_건드린다():
    """다른 프로세스가 지금 재색인 중일 수 있다. 만들다 만 인덱스는 별칭이 아직 없어서
    '지워도 되는' 것처럼 보인다."""
    fresh = "notices_20261005120000"
    client = 쌓인것(**{fresh: (set(), NOW - 600_000)})      # 10분 전
    assert fresh not in 후보(client), "재색인 중일 수 있는 인덱스를 지우려 했다"
    assert "시간이 안 됐습니다" in 건너뜀(client)[fresh]


def test_나이_문턱_바로_위는_고른다():
    old_enough = "notices_20261005000000"
    client = 쌓인것(**{old_enough: (set(), NOW - int(index_ref.PRUNE_MIN_AGE_SEC * 1000) - 60_000)})
    assert old_enough in 후보(client)


def test_다른_별칭이_붙어_있으면_후보가_아니다():
    client = 쌓인것()
    client.indices.indices[OLD1] = {"notices_backup"}
    assert OLD1 not in 후보(client)


def test_별칭이_여럿을_가리키면_아무것도_안_고른다():
    """전환이 중간에 끊긴 상태. 어느 쪽이 원본인지 모르는 채로 지우면 안 된다."""
    client = 쌓인것()
    client.indices.indices[PREV] = {ALIAS}
    assert 후보(client) == []


def test_만든_시각을_못_읽으면_후보가_아니다():
    client = 쌓인것()
    client.cat.created[OLD1] = 0
    assert OLD1 not in 후보(client)


def test_사용자의_지금_상태에서는_지울_것이_없다():
    """#12 정리 직후의 실제 상태. 자동으로 돌아도 아무것도 안 지워져야 한다."""
    client = FakeClient(
        {LIVE: {ALIAS}, PREV: set(), "notices_v4": set()},
        created={LIVE: NOW - 3 * 24 * HOUR, PREV: NOW - 7 * 24 * HOUR,
                 "notices_v4": NOW - 13 * 24 * HOUR},
    )
    assert 후보(client) == []


# ── 지우기 ──────────────────────────────────────────────────────────────
def test_이름을_받아_지운다():
    client = 쌓인것()
    r = index_ref.drop([OLD1, OLD2], client)
    assert r == {"dropped": [OLD1, OLD2], "failed": {}}
    assert client.indices.deleted == [OLD1, OLD2]
    assert OLD1 not in client.indices.indices


def test_하나가_실패해도_나머지는_계속한다():
    """지우다 멈추면 어디까지 지웠는지가 흐려진다."""
    client = 쌓인것()
    r = index_ref.drop([OLD1, "없는인덱스", OLD2], client)
    assert r["dropped"] == [OLD1, OLD2]
    assert "없는인덱스" in r["failed"]


# ── 따로 부르는 길 ───────────────────────────────────────────────────────
def test_dry_run이_기본_동작이고_아무것도_안_지운다():
    client = 쌓인것()
    r = dictionary.prune(client=client, dry_run=True)
    assert r["ok"] and r["dry_run"]
    assert r["candidates"] == [OLD1, OLD2]
    assert r["dropped"] == []
    assert client.indices.deleted == [], "dry_run 인데 지웠다"


def test_dry_run을_끄면_지운다():
    client = 쌓인것()
    r = dictionary.prune(client=client, dry_run=False)
    assert r["dropped"] == [OLD1, OLD2]
    assert client.indices.deleted == [OLD1, OLD2]


def test_건너뛴_이유를_같이_돌려준다():
    """왜 안 지웠는지 안 보이면 '안 지워졌다' 와 '지울 것이 없었다' 를 가릴 수 없다."""
    client = 쌓인것(**{"notices": (set(), NOW - 200 * 24 * HOUR)})
    r = dictionary.prune(client=client, dry_run=True)
    assert [s["name"] for s in r["skipped"]] == ["notices"]
    assert "자동 생성된 이름이 아닙니다" in r["skipped"][0]["reason"]


def test_재색인이_돌고_있으면_거부한다():
    """재색인이 만든 인덱스를 그 재색인이 전환하기 전에 지울 수 있다."""
    client = 쌓인것()
    assert dictionary._lock.acquire(blocking=False)
    try:
        r = dictionary.prune(client=client, dry_run=False)
    finally:
        dictionary._lock.release()
    assert not r["ok"] and r["reason"] == "already_running"
    assert client.indices.deleted == []


def test_자물쇠를_반드시_놓는다():
    client = 쌓인것()
    dictionary.prune(client=client, dry_run=True)
    assert dictionary._lock.acquire(blocking=False), "자물쇠를 들고 있다"
    dictionary._lock.release()


def test_목록을_못_읽으면_지우지_않고_이유를_말한다():
    client = 쌓인것()
    client.cat.indices = lambda **kw: (_ for _ in ()).throw(Boom("접속 불가"))
    r = dictionary.prune(client=client, dry_run=False)
    assert not r["ok"] and r["dropped"] == []
    assert client.indices.deleted == []


# ── 재색인 뒤에 저절로 도는 쪽 ────────────────────────────────────────────
def test_재색인_뒤에_치운다():
    client = 쌓인것()
    assert dictionary._prune_quietly(client) == [OLD1, OLD2]


def test_DICT_PRUNE가_0이면_안_치운다(monkeypatch):
    monkeypatch.setenv("DICT_PRUNE", "0")
    client = 쌓인것()
    assert dictionary._prune_quietly(client) == []
    assert client.indices.deleted == []


def test_치우다_터져도_재색인_결과를_망치지_않는다():
    """여기서 예외가 올라가면 멀쩡히 끝난 재색인이 실패로 보고된다."""
    client = 쌓인것()
    client.cat.indices = lambda **kw: (_ for _ in ()).throw(Boom("접속 불가"))
    assert dictionary._prune_quietly(client) == []       # 예외가 새지 않는다
