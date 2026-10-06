"""수집할 때 <hr> 경계를 남기고, 원문 복원에 그걸 먼저 쓴다(#9).

빗썸은 재개·연기 안내를 본문 **위에** `<hr>`로 구분해 덧붙인다. 전에는 태그를 그냥 지워서
경계가 사라졌고, '마지막 안녕하세요부터가 원문'으로 되짚어야 했다. 그 규칙은 실측 2,439건에서
반례가 없었지만, 되짚기인 만큼 어긋날 수 있는 꼴이 있다 — 아래 검사가 그 꼴을 든다.

구분선은 **새로 수집한 공지에만** 있다. 이미 색인된 공지에는 없으므로 인사 규칙이 계속 필요하다.
"""

from __future__ import annotations

import pytest

from notice_ai.factcheck import (
    BLOCK_SEP,
    MIN_ORIGINAL,
    original_version,
    strip_block_sep,
)
from notice_ai.scraper_client import html_to_text


def esc(html: str) -> str:
    """수집이 받는 꼴. content 는 이중 이스케이프 상태로 온다."""
    return html.replace("<", "&lt;").replace(">", "&gt;")


ORIGINAL = ("안녕하세요. 빗썸입니다. 메가이더(MEGA) 네트워크 업그레이드로 입출금을 중지합니다. "
            "중지 시점은 2026년 9월 18일 15:00이며, 재개 시점은 네트워크 안정화 후 별도 "
            "안내드립니다. 회원님의 양해를 부탁드립니다.")


# ── 수집: 경계를 남기는가 ────────────────────────────────────────────────
@pytest.mark.parametrize("tag", ["<hr>", "<hr/>", "<hr />", '<hr style="border:1px">', "<HR>"])
def test_hr의_여러_표기를_모두_잡는다(tag):
    out = html_to_text(esc(f"<p>위</p>{tag}<p>아래</p>"))
    assert out.count(BLOCK_SEP) == 1, f"{tag} 를 놓쳤다"


def test_hr이_없으면_구분선도_없다():
    assert BLOCK_SEP not in html_to_text(esc("<p>안녕하세요. 중지합니다.</p>"))


def test_여러_번_쌓인_업데이트도_각각_남는다():
    """USDT 등 실제로 여러 번 쌓인 공지가 있다."""
    out = html_to_text(esc("<p>재개</p><hr><p>연기</p><hr><p>안녕하세요. 중지</p>"))
    assert out.count(BLOCK_SEP) == 2


def test_경계_말고는_전과_같다():
    """구분선을 떼면 예전 출력과 같아야 한다 — 수집 결과가 통째로 달라지면 안 된다."""
    html = esc("<p>첫째 줄</p><p>둘째 줄</p><ul><li>항목</li></ul>")
    assert BLOCK_SEP not in html_to_text(html)
    assert html_to_text(html) == "첫째 줄\n둘째 줄\n항목"


def test_빈_입력에도_안_터진다():
    assert html_to_text("") == ""


# ── 복원: 구분선을 먼저 쓴다 ─────────────────────────────────────────────
def test_구분선으로_업데이트를_떼어낸다():
    body = html_to_text(esc(f"<p>2026년 9월 20일 입출금이 재개되었습니다.</p><hr><p>{ORIGINAL}</p>"))
    _, got, notes = original_version("메가이더(MEGA) 입출금 중지 안내 (09/20 재개)", body)

    assert got == ORIGINAL
    assert "재개되었습니다" not in got
    assert any("구분선" in n for n in notes)


def test_업데이트가_인사_없이_붙은_경우():
    """실측 1,041건이 이 꼴이다. 인사 규칙으로도 되지만 구분선이 더 곧바르다."""
    body = html_to_text(esc(f"<p>정상화되었습니다.</p><hr><p>{ORIGINAL}</p>"))
    assert original_version("제목", body)[1] == ORIGINAL


def test_마지막_구분선_뒤가_원문이다():
    """업데이트는 위에 쌓인다. 여러 번 쌓였으면 맨 아래가 최초 버전이다."""
    body = html_to_text(esc(f"<p>재개</p><hr><p>연기 안내입니다.</p><hr><p>{ORIGINAL}</p>"))
    got = original_version("제목", body)[1]
    assert got == ORIGINAL
    assert "연기" not in got


def test_원문_안에_안녕하세요가_또_있어도_제대로_가른다():
    """인사 규칙이 어긋나는 꼴. 구분선이 있으면 이런 걸 안 따진다.

    인사 규칙은 **마지막** '안녕하세요'에서 자른다. 원문 안쪽에 또 있으면 뒤쪽에서 자르게
    되는데, 그러면 잘라낸 원문이 짧아져 `MIN_ORIGINAL` 가드가 걸리고 **아무것도 안 자른다** —
    업데이트 안내가 참고 본문에 그대로 남는다. 초안이 '재개되었습니다' 를 베낄 수 있다.
    """
    inner = ('안녕하세요. 빗썸입니다. 아래와 같이 안내드립니다. 고객센터에 "안녕하세요" 로 '
             '시작하는 안내가 추가되었으니 확인 부탁드립니다. 중지 시점은 2026년 9월 18일 15:00입니다.')
    body = html_to_text(esc(f"<p>재개되었습니다.</p><hr><p>{inner}</p>"))

    assert original_version("제목", body)[1] == inner, "구분선 기준이 안 먹었다"
    # 같은 본문에서 구분선만 없으면(옛 문서) 업데이트가 그대로 남는다
    없는쪽 = original_version("제목", body.replace(BLOCK_SEP, ""))[1]
    assert "재개되었습니다" in 없는쪽


def test_원문이_너무_짧으면_구분선이_있어도_안_자른다():
    """장식으로 쓴 <hr> 일 수 있다. 판단이 불확실하면 자르지 않는다."""
    body = html_to_text(esc("<p>안녕하세요. 긴 원문입니다. " + "가" * 150 + "</p><hr><p>짧은 꼬리</p>"))
    _, got, notes = original_version("제목", body)
    assert "긴 원문입니다" in got, "장식 구분선에서 잘라 원문을 잃었다"
    assert notes == []
    assert len("짧은 꼬리") < MIN_ORIGINAL


def test_구분선_위가_비어_있으면_안_자른다():
    body = html_to_text(esc(f"<hr><p>{ORIGINAL}</p>"))
    _, got, notes = original_version("제목", body)
    assert got == ORIGINAL
    assert notes == []


# ── 구분선은 밖으로 새지 않는다 ───────────────────────────────────────────
def test_내보내는_본문에_구분선이_안_남는다():
    """프롬프트에 들어가는 본문이다. 남으면 LLM 이 베낄 수 있다."""
    body = html_to_text(esc(f"<p>재개</p><hr><p>{ORIGINAL}</p>"))
    assert BLOCK_SEP not in original_version("제목", body)[1]


def test_원문_안쪽의_장식_구분선도_떼어_낸다():
    """자르지 않고 그대로 내보내는 경우에도 글자는 남지 않아야 한다."""
    body = html_to_text(esc("<p>안녕하세요. " + "가" * 150 + "</p><hr><p>" + "나" * 150 + "</p>"))
    _, got, _ = original_version("제목", body)
    assert BLOCK_SEP in body and BLOCK_SEP not in got


def test_제목에_섞여_들어와도_떼어_낸다():
    assert BLOCK_SEP not in original_version(f"제목{BLOCK_SEP}입니다", "본문")[0]


def test_구분선을_떼면_빈_줄_하나로_정리된다():
    assert strip_block_sep(f"위\n\n{BLOCK_SEP}\n\n아래") == "위\n\n아래"
    assert strip_block_sep(f"{BLOCK_SEP}가운데{BLOCK_SEP}") == "가운데"
    assert strip_block_sep("") == ""


# ── 옛 문서는 전과 똑같이 ────────────────────────────────────────────────
def test_구분선이_없으면_인사_규칙으로_돈다():
    """이미 색인된 4천여 건에는 구분선이 없다. 동작이 바뀌면 안 된다."""
    body = f"안녕하세요. 빗썸입니다.\n재개되었습니다.\n\n{ORIGINAL}"
    _, got, notes = original_version("제목", body)
    assert got == ORIGINAL
    assert any("본문 위에" in n for n in notes)


def test_업데이트가_없는_옛_공지는_그대로_둔다():
    body = "안녕하세요. 빗썸입니다.\n중지합니다."
    assert original_version("제목", body) == ("제목", body, [])


# ── 이미 색인된 공지에 적용하는 길 ────────────────────────────────────────
#
# 구분선은 새로 수집한 공지에만 생긴다. 저장해 둔 원본 HTML이 없어서(수집은 변환된
# raw_text만 넣는다) 이미 색인된 공지에 적용하려면 본문을 다시 받는 수밖에 없다.
class FakeScraper:
    def __init__(self, bodies: dict[int, str]):
        self.bodies = bodies
        self.detail_calls: list[int] = []

    def list_page(self, cat_id, page):
        if page > 1:
            return [], len(self.bodies)
        items = [{"id": i, "title": f"공지 {i}", "categoryName1": "입출금",
                  "publicationDateTime": "2026-09-20 10:00:00"} for i in self.bodies]
        return items, len(items)

    def detail(self, notice_id):
        self.detail_calls.append(notice_id)
        return {"content": self.bodies[notice_id]}


class FakeOS:
    """있는 문서 집합만 들고, index/update 호출을 기록한다."""

    def __init__(self, existing: set[str]):
        self.existing = set(existing)
        self.indexed: list[tuple[str, dict]] = []
        self.updated: list[tuple[str, dict]] = []

    def exists(self, index, id):
        return id in self.existing

    def index(self, index, id, body):
        self.indexed.append((id, body))
        self.existing.add(id)

    def update(self, index, id, body):
        self.updated.append((id, body))


@pytest.fixture
def 수집(monkeypatch):
    from notice_ai import collector

    def 돌리기(bodies, existing, **kw):
        os_fake = FakeOS(existing)
        monkeypatch.setattr(collector, "get_client", lambda: os_fake)
        monkeypatch.setattr(collector.index_ref, "target", lambda *a, **k: "notices_test")
        scraper = FakeScraper(bodies)
        n = collector.collect_category("입출금", scraper, **kw)
        return n, os_fake, scraper

    return 돌리기


URL = "https://feed.bithumb.com/notice/{}"
HTML = esc(f"<p>재개</p><hr><p>{ORIGINAL}</p>")


def test_평소에는_이미_있는_공지를_건너뛴다(수집):
    """상세 요청까지 생략한다 — feed.bithumb.com 은 429 에 민감하다."""
    n, os_fake, scraper = 수집({1: HTML}, existing={URL.format(1)})
    assert n == 0
    assert scraper.detail_calls == [], "건너뛸 공지의 본문을 받았다"
    assert os_fake.updated == [] and os_fake.indexed == []


def test_refresh_body면_이미_있는_공지도_본문을_다시_받는다(수집):
    n, os_fake, scraper = 수집({1: HTML}, existing={URL.format(1)}, refresh_body=True)
    assert n == 1
    assert scraper.detail_calls == [1]


def test_본문만_갈아_끼운다_임베딩을_날리지_않는다(수집):
    """통째로 색인하면 embedding 이 사라지고, embed 는 '임베딩 없는 문서' 만 채우므로
    그 공지는 조용히 벡터 검색에서 빠진다. 눈에 안 보이는 손실이다."""
    _, os_fake, _ = 수집({1: HTML}, existing={URL.format(1)}, refresh_body=True)

    assert os_fake.indexed == [], "문서를 통째로 덮어썼다 — 임베딩이 날아간다"
    assert len(os_fake.updated) == 1
    doc_id, body = os_fake.updated[0]
    assert doc_id == URL.format(1)
    assert set(body["doc"]) == {"raw_text"}, f"raw_text 말고 다른 것도 건드린다: {set(body['doc'])}"


def test_다시_받은_본문에_구분선이_들어간다(수집):
    _, os_fake, _ = 수집({1: HTML}, existing={URL.format(1)}, refresh_body=True)
    raw = os_fake.updated[0][1]["doc"]["raw_text"]
    assert BLOCK_SEP in raw
    assert original_version("제목", raw)[1] == ORIGINAL


def test_새_공지는_refresh_body여도_평소처럼_색인된다(수집):
    _, os_fake, _ = 수집({1: HTML}, existing=set(), refresh_body=True)
    assert len(os_fake.indexed) == 1 and os_fake.updated == []
    assert BLOCK_SEP in os_fake.indexed[0][1]["raw_text"]


def test_공지_조회가_구분선을_내보내지_않는다(monkeypatch):
    """프론트가 그리는 '원문 미리보기' 다. 내부 장치가 API 로 새면 안 된다."""
    from notice_ai import drafting

    raw = html_to_text(esc(f"<p>재개되었습니다.</p><hr><p>{ORIGINAL}</p>"))
    assert BLOCK_SEP in raw
    monkeypatch.setattr(drafting, "_fetch_notice", lambda url: {
        "source_url": url, "title": "제목", "raw_text": raw, "categories": ["입출금"],
        "published_at": "2026-09-20 10:00:00", "tickers": ["MEGA"],
    })
    d = drafting.get_notice_detail("https://feed.bithumb.com/notice/1")

    assert BLOCK_SEP not in d["body"], "원문 미리보기에 구분선이 남았다"
    assert BLOCK_SEP not in d["original_body"]
    assert "재개되었습니다" in d["body"]           # 원문은 통째로 보여 준다
    assert "재개되었습니다" not in d["original_body"]   # 초안이 참고하는 쪽에서는 빠진다
