"""CLI: python -m notice_ai.cli <command>

  setup-index [--recreate]   Nori+kNN 인덱스 생성
  collect [--pages N]        공지 수집 → 색인
  embed                      임베딩 백필(벡터 검색용, Bedrock 필요)
  search <검색어> [--category 이름] [--hybrid] [--rerank] [--limit N]
"""

from __future__ import annotations

import argparse
import logging


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    p = argparse.ArgumentParser(prog="notice-ai")
    sub = p.add_subparsers(dest="command", required=True)

    ps = sub.add_parser("setup-index")
    ps.add_argument("--recreate", action="store_true")

    pc = sub.add_parser("collect", help="공지 수집(스크래핑)")
    pc.add_argument("--category", default=None, help="카테고리명(생략 시 전체)")
    pc.add_argument("--max-pages", type=int, default=None, help="카테고리당 최대 페이지(테스트용)")
    pc.add_argument("--no-body", action="store_true", help="본문 없이 목록만 색인(빠름)")

    pi = sub.add_parser("ingest-csv")
    pi.add_argument("path", help="공지 CSV 경로")

    sub.add_parser("embed")

    px = sub.add_parser("search")
    px.add_argument("query")
    px.add_argument("--category", default=None)
    px.add_argument("--hybrid", action="store_true")
    px.add_argument("--rerank", action="store_true")
    px.add_argument("--limit", type=int, default=10)

    a = p.parse_args()

    if a.command == "setup-index":
        from notice_ai.index_setup import create_index

        create_index(recreate=a.recreate)
    elif a.command == "collect":
        from notice_ai.collector import collect_all, collect_category

        fetch_body = not a.no_body
        if a.category:
            n = collect_category(a.category, max_pages=a.max_pages, fetch_body=fetch_body)
        else:
            n = collect_all(max_pages=a.max_pages, fetch_body=fetch_body)
        print(f"신규 수집·색인 {n}건")
    elif a.command == "ingest-csv":
        from notice_ai.ingest import ingest_csv

        print(f"CSV 색인 {ingest_csv(a.path)}건")
    elif a.command == "embed":
        from notice_ai.indexing import embed_missing

        print(f"임베딩 {embed_missing()}건")
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
