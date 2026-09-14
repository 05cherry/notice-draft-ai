"""공유 토큰 접근 통제 + CORS 허용 주소 — 공개 인터넷에 올릴 때 켠다.

`API_TOKEN` 환경변수가 있을 때만 막는다. 없으면(로컬 개발) 지금까지와 똑같이 아무나 부를 수 있다.
토큰을 내는 방법은 세 가지고, 아래 순서로 본다.

    X-API-Token: <토큰>            프론트·스크립트가 쓰는 기본 방법
    Authorization: Bearer <토큰>   curl 등에서 익숙한 방법
    쿠키                            브라우저로 /docs·/ui 를 열 때. 주소 뒤에 ?token=... 을 한 번
                                    붙이면 쿠키에 담고 주소에서 토큰을 지운 뒤 되돌려보낸다

토큰 없이 통과하는 것은 둘뿐이다. `/health`(Render가 서버 생존을 확인하는 경로)와
CORS 사전 요청(OPTIONS). 사전 요청에는 헤더를 붙일 수 없어서 막으면 브라우저가 본 요청을 아예 안 보낸다.

`ALLOWED_ORIGINS`(쉼표로 구분)로 CORS를 프론트 주소만 남기게 좁힌다. 기본은 전부 허용(`*`).

주의 — 이 토큰은 팀이 나눠 갖는 하나의 열쇠지 사용자별 인증이 아니다. 브라우저에 넣는 순간
그 화면을 여는 사람은 누구나 토큰을 꺼내 볼 수 있다. 사용자별 인증은 #6에서 한다.
"""

from __future__ import annotations

import os
import secrets

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, RedirectResponse

COOKIE_NAME = "notice_api_token"
COOKIE_MAX_AGE = 60 * 60 * 24 * 30      # 30일
OPEN_PATHS = frozenset({"/health"})     # 토큰 없이 통과(Render 상태 확인)

_UNAUTHORIZED = {
    "detail": "접근 토큰이 필요합니다. X-API-Token 헤더에 토큰을 넣어 주세요"
              " (브라우저에서는 주소 뒤에 ?token=... 을 한 번 붙이면 기억합니다).",
    "kind": "unauthorized",
}


def allowed_origins() -> list[str]:
    """CORS로 허용할 프론트 주소. 기본은 전부 허용."""
    raw = os.environ.get("ALLOWED_ORIGINS", "*")
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()] or ["*"]


def _presented(request: Request) -> str | None:
    """요청이 내민 토큰(헤더 > Bearer > 쿠키)."""
    if header := request.headers.get("x-api-token"):
        return header
    auth = request.headers.get("authorization", "")
    if auth[:7].lower() == "bearer ":
        return auth[7:].strip()
    return request.cookies.get(COOKIE_NAME)


def install(app: FastAPI) -> None:
    """토큰 검사와 CORS를 붙인다. api.py가 앱을 만든 직후 한 번 부른다.

    순서가 중요하다. 나중에 등록한 미들웨어가 바깥에 놓이므로 CORS를 뒤에 붙여야
    401 응답에도 CORS 헤더가 붙는다. 안 그러면 브라우저는 안내 문구 대신 '연결 실패'만 본다.
    """
    token = os.environ.get("API_TOKEN") or None

    if token:
        @app.middleware("http")
        async def require_token(request: Request, call_next):
            if request.method == "OPTIONS" or request.url.path in OPEN_PATHS:
                return await call_next(request)

            # 브라우저로 연 경우: ?token=... 을 쿠키로 옮기고 주소에서 지운다
            # (주소창·접속 기록·리퍼러에 토큰이 남지 않게).
            if (q := request.query_params.get("token")) and secrets.compare_digest(q, token):
                res = RedirectResponse(str(request.url.remove_query_params("token")), status_code=303)
                res.set_cookie(COOKIE_NAME, token, max_age=COOKIE_MAX_AGE, httponly=True,
                               samesite="lax", secure=request.url.scheme == "https")
                return res

            given = _presented(request)
            if given and secrets.compare_digest(given, token):
                return await call_next(request)
            return JSONResponse(status_code=401, content=_UNAUTHORIZED)

    app.add_middleware(
        CORSMiddleware,
        allow_origins=allowed_origins(),
        allow_methods=["*"],
        allow_headers=["*"],
    )
