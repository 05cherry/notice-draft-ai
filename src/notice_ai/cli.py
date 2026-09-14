"""CLI: python -m notice_ai.cli <command>

  setup-index [--recreate] [--pos-stoptags narrow|default]
                             Nori+kNN 인덱스 생성(이름은 NOTICE_INDEX, 기본 notices_v3)
  reindex --source 이름      기존 인덱스 문서를 NOTICE_INDEX 인덱스로 복사
  collect [--pages N]        공지 수집 → 색인 → 새 공지 임베딩(--no-embed로 생략)
  ingest-csv 경로            사내 CSV 색인 → 새 공지 임베딩(--no-embed로 생략)
  embed                      임베딩 백필(벡터 검색용, Bedrock 필요)
  search <검색어> [--category 이름] [--hybrid] [--rerank] [--limit N]
"""

from __future__ import annotations

import argparse
import logging


def _embed_new() -> None:
    """새로 색인한 공지(임베딩 없는 것)에 임베딩을 채운다. 없으면 유형 추정·비슷한 공지에서 빠진다.
    Bedrock이 안 되면 수집 결과는 그대로 두고 안내만 한다."""
    from notice_ai.indexing import embed_missing

    try:
        print(f"새 공지 임베딩 {embed_missing()}건")
    except Exception as e:
        print(f"임베딩은 건너뜀({type(e).__name__}). 나중에 `py -m notice_ai.cli embed`로 채우세요.")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="notice-ai")
    sub = p.add_subparsers(dest="command", required=True)

    ps = sub.add_parser("setup-index")
    ps.add_argument("--recreate", action="store_true")
    ps.add_argument("--pos-stoptags", choices=["narrow", "default"], default="narrow",
                    help="품사 필터: narrow=조사·어미·하다접미사·기호만(기본, v3), default=Nori 기본(v2)")

    pr = sub.add_parser("reindex", help="기존 인덱스 → 현재 NOTICE_INDEX로 복사(재수집 없이 매핑 변경)")
    pr.add_argument("--source", required=True, help="원본 인덱스 이름(예: notices)")

    pc = sub.add_parser("collect", help="공지 수집(스크래핑)")
    pc.add_argument("--category", default=None, help="카테고리명(생략 시 전체)")
    pc.add_argument("--max-pages", type=int, default=None, help="카테고리당 최대 페이지(테스트용)")
    pc.add_argument("--no-body", action="store_true", help="본문 없이 목록만 색인(빠름)")
    pc.add_argument("--no-embed", action="store_true", help="새 공지 임베딩 생략(나중에 embed로)")

    pi = sub.add_parser("ingest-csv")
    pi.add_argument("path", help="공지 CSV 경로")
    pi.add_argument("--no-embed", action="store_true", help="새 공지 임베딩 생략(나중에 embed로)")

    pe = sub.add_parser("embed", help="embedding 없는 문서에 임베딩 채우기(Bedrock, 호출 비용)")
    pe.add_argument("--limit", type=int, default=None, help="이 건수까지만(시험용)")

    px = sub.add_parser("search")
    px.add_argument("query")
    px.add_argument("--category", default=None)
    px.add_argument("--hybrid", action="store_true")
    px.add_argument("--rerank", action="store_true")
    px.add_argument("--limit", type=int, default=10)

    a = p.parse_args()

    if a.command == "setup-index":
        from notice_ai.index_setup import NARROW_STOPTAGS, create_index

        create_index(recreate=a.recreate, pos_stoptags=NARROW_STOPTAGS if a.pos_stoptags == "narrow" else None)
    elif a.command == "reindex":
        from notice_ai.index_setup import reindex_from

        print(reindex_from(a.source))
    elif a.command == "collect":
        from notice_ai.collector import collect_all, collect_category

        fetch_body = not a.no_body
        if a.category:
            n = collect_category(a.category, max_pages=a.max_pages, fetch_body=fetch_body)
        else:
            n = collect_all(max_pages=a.max_pages, fetch_body=fetch_body)
        print(f"신규 수집·색인 {n}건")
        if n and not a.no_embed:
            _embed_new()
    elif a.command == "ingest-csv":
        from notice_ai.ingest import ingest_csv

        n = ingest_csv(a.path)
        print(f"CSV 색인 {n}건")
        if n and not a.no_embed:
            _embed_new()
    elif a.command == "embed":
        from notice_ai.indexing import embed_missing

        print(f"임베딩 {embed_missing(limit=a.limit)}건")
    elif a.command == "search":
        if a.hybrid:
            from notice_ai.search import hybrid_search

            hits = hybrid_search(
                a.query, {"category": a.category} if a.category else {},
                use_rerank=a.rerank, category_name=a.category, limit=a.limit,
            )
        else:
            from notice_ai.search import search_notices

            hits = search_notices(a.query, category_name=a.category, limit=a.limit)
        for h in hits:
            print(f"[{h.score:.3f}] ({'/'.join(h.categories)}) {h.title}")
            print(f"        {h.published_at}  {h.source_url}")


if __name__ == "__main__":
    main()
