"""공유 토큰 접근 통제 — 자기 앱을 만들어 검사한다. 네트워크·OpenSearch 안 쓴다.

`auth.install(app)` 이 `API_TOKEN` 을 install 시점에 읽으므로, 테스트마다 작은 앱을 새로
세우면 환경변수를 서로 간섭 없이 바꿀 수 있다(api 모듈을 import 하지 않는 이유).
"""

from __future__ import annotations

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from notice_ai import auth
from notice_ai.config import ConfigError

TOKEN = "test-token-abc123"      # 헤더로도 보내야 하므로 ASCII. 한글 토큰은 아래에서 따로 본다


def 앱(monkeypatch, token: str | None = TOKEN) -> TestClient:
    if token is None:
        monkeypatch.delenv("API_TOKEN", raising=False)
    else:
        monkeypatch.setenv("API_TOKEN", token)

    app = FastAPI()

    @app.get("/health")
    def health():
        return {"ok": True}

    @app.get("/thing")
    def get_thing():
        return {"ok": True}

    @app.post("/thing")
    def post_thing(payload: dict):
        return {"ok": True, "got": payload}

    auth.install(app)
    return TestClient(app, follow_redirects=False)


# ── 막는가 ──────────────────────────────────────────────────────────────
def test_토큰이_설정되지_않으면_아무나_부른다(monkeypatch):
    """로컬 개발은 지금까지와 똑같이 돈다."""
    assert 앱(monkeypatch, token=None).get("/thing").status_code == 200


def test_토큰이_없으면_401(monkeypatch):
    res = 앱(monkeypatch).get("/thing")
    assert res.status_code == 401
    assert res.json()["kind"] == "unauthorized"


def test_틀린_토큰은_401(monkeypatch):
    res = 앱(monkeypatch).get("/thing", headers={"X-API-Token": "wrong-token"})
    assert res.status_code == 401


def test_비ASCII_토큰을_보내도_401이지_500이_아니다(monkeypatch):
    """`secrets.compare_digest` 는 비ASCII 문자열에 TypeError 를 낸다.

    그대로 두면 아무나 `?token=한글` 한 번으로 서버 오류를 만들 수 있었다.
    토큰이 뚫리는 건 아니지만, 안내 문구가 나와야 할 자리에 500 이 났다.
    """
    res = 앱(monkeypatch).get("/thing?token=%ED%95%9C%EA%B8%80")   # '한글'
    assert res.status_code == 401


def test_한글_토큰은_뜰_때_거부한다(monkeypatch):
    """쿠키·헤더는 HTTP 규격상 ASCII 다. 한글 토큰으로는 애초에 돌 수 없으므로
    요청마다 500 을 내지 말고 뜰 때 알려 준다."""
    with pytest.raises(ConfigError, match="ASCII"):
        앱(monkeypatch, token="한글토큰-xyz")


@pytest.mark.parametrize("headers", [
    {"X-API-Token": TOKEN},
    {"Authorization": f"Bearer {TOKEN}"},
    {"Authorization": f"bearer {TOKEN}"},
])
def test_헤더로_낸_토큰을_받는다(monkeypatch, headers):
    assert 앱(monkeypatch).get("/thing", headers=headers).status_code == 200


def test_쿠키로_낸_토큰을_받는다(monkeypatch):
    client = 앱(monkeypatch)
    client.cookies.set(auth.COOKIE_NAME, TOKEN)
    assert client.get("/thing").status_code == 200


# ── 브라우저로 열 때 ────────────────────────────────────────────────────
def test_GET에_붙인_token은_쿠키로_옮기고_주소에서_지운다(monkeypatch):
    res = 앱(monkeypatch).get("/thing?token=" + TOKEN)
    assert res.status_code == 303
    assert "token=" not in res.headers["location"]
    assert res.headers["location"].startswith("/thing")
    assert auth.COOKIE_NAME in res.headers.get("set-cookie", "")


def test_되돌려보낼_곳은_경로만_적는다(monkeypatch):
    """절대 주소로 적으면 프록시 뒤에서 http:// 로 돌려보내고, 브라우저가 그 평문
    요청에 토큰 쿠키를 실어 보낸다."""
    res = 앱(monkeypatch).get("/thing?token=" + TOKEN)
    assert not res.headers["location"].startswith("http")


def test_다른_질의는_남긴다(monkeypatch):
    res = 앱(monkeypatch).get(f"/thing?token={TOKEN}&limit=5")
    assert "limit=5" in res.headers["location"]


def test_HTTPS로_왔으면_쿠키에_Secure를_붙인다(monkeypatch):
    res = 앱(monkeypatch).get("/thing?token=" + TOKEN,
                              headers={"X-Forwarded-Proto": "https"})
    assert "secure" in res.headers["set-cookie"].lower()


def test_POST에_붙인_token은_리다이렉트하지_않는다(monkeypatch):
    """#46 — 303 은 본문 있는 요청을 GET 으로 바꿔 버린다. POST 를 그 길로 보내면
    메서드가 갈려 405 가 되고, `curl -X POST '...?token=...'` 이 조용히 아무것도 안 했다.
    """
    res = 앱(monkeypatch).post("/thing?token=" + TOKEN, json={"a": 1})
    assert res.status_code == 200, f"POST 가 리다이렉트로 샜다({res.status_code})"
    assert res.json()["got"] == {"a": 1}


# ── 토큰 없이 통과하는 둘 ────────────────────────────────────────────────
def test_health는_토큰_없이_통과한다(monkeypatch):
    """Render 가 서버 생존을 확인하는 경로. 막으면 배포가 실패로 뜬다."""
    assert 앱(monkeypatch).get("/health").status_code == 200


@pytest.mark.parametrize("deep", ["true", "1", "yes", "on", "T"])
def test_health_deep은_토큰을_받는다(monkeypatch, deep):
    """인덱스 이름·공지 건수·LLM 모델을 알려 주는 쪽."""
    assert 앱(monkeypatch).get(f"/health?deep={deep}").status_code == 401


def test_deep이_거짓이면_health는_열려_있다(monkeypatch):
    assert 앱(monkeypatch).get("/health?deep=0").status_code == 200


def test_CORS_사전요청은_통과한다(monkeypatch):
    """사전 요청에는 헤더를 붙일 수 없다. 막으면 브라우저가 본 요청을 아예 안 보낸다."""
    res = 앱(monkeypatch).options("/thing", headers={
        "Origin": "https://example.com",
        "Access-Control-Request-Method": "POST",
    })
    assert res.status_code == 200


def test_401에도_CORS_헤더가_붙는다(monkeypatch):
    """안 붙으면 브라우저는 안내 문구 대신 '연결 실패'만 본다."""
    res = 앱(monkeypatch).get("/thing", headers={"Origin": "https://example.com"})
    assert res.status_code == 401
    assert "access-control-allow-origin" in {k.lower() for k in res.headers}


# ── 허용 주소 ───────────────────────────────────────────────────────────
def test_허용_주소_기본은_전부(monkeypatch):
    monkeypatch.delenv("ALLOWED_ORIGINS", raising=False)
    assert auth.allowed_origins() == ["*"]


def test_허용_주소는_쉼표로_나누고_끝_슬래시를_뗀다(monkeypatch):
    monkeypatch.setenv("ALLOWED_ORIGINS", " https://a.example.com/ ,https://b.example.com")
    assert auth.allowed_origins() == ["https://a.example.com", "https://b.example.com"]
