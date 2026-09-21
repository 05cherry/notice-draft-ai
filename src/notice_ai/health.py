"""서비스 상태 점검(/health?deep=true).

검색 서버(OpenSearch)·임베딩(Bedrock)·LLM을 본다. LLM은 초안을 생성해 보지는 않고(비용)
모델 목록 조회로 키가 통하는지만 확인한다 — 토큰 비용이 없고, 키가 틀렸는지를 /draft 전에 알 수 있다.
status: ok(전부 정상) / degraded(검색은 되지만 임베딩·LLM 쪽 문제 — 비슷한 공지·유형 추정·초안 생성이 제한됨)
        / down(검색 서버에 못 붙음 — 검색·참고 공지 선택이 안 됨).
"""

from __future__ import annotations

from typing import Callable

from notice_ai import config, llm


def _err(e: Exception) -> str:
    return f"{type(e).__name__}: {' '.join(str(e).split())[:120]}"


def check(*, embed: Callable | None = None, client=None) -> dict:
    out: dict = {}
    try:
        if client is None:
            from notice_ai.opensearch_client import get_client

            client = get_client()
        idx = config.INDEX_NAME
        total = client.count(index=idx)["count"]
        with_vec = client.count(index=idx, body={"query": {"exists": {"field": "embedding"}}})["count"]
        out["opensearch"] = {"ok": True, "index": idx, "docs": total, "with_embedding": with_vec}
    except Exception as e:
        out["opensearch"] = {"ok": False, "error": _err(e)}

    try:
        if embed is None:
            from notice_ai.embeddings import embed_query as embed
        vec = embed("상태 확인")
        out["embedding"] = {"ok": bool(vec), "dim": len(vec)}
    except Exception as e:
        out["embedding"] = {"ok": False, "error": _err(e)}

    out["llm"] = llm.settings()
    if out["llm"]["configured"]:
        # 키가 '있다'와 '통한다'는 다르다. 안 통하면 초안 생성이 502로 떨어지므로 여기서 잡는다.
        auth = llm.check_auth()
        out["llm"]["key_ok"] = auth["ok"]
        if auth.get("error"):
            out["llm"]["error"] = auth["error"]

    llm_ok = out["llm"]["configured"] and out["llm"].get("key_ok") is not False
    if not out["opensearch"]["ok"]:
        out["status"] = "down"
    elif out["embedding"]["ok"] and llm_ok:
        out["status"] = "ok"
    else:
        out["status"] = "degraded"
    return out
