"""CLI: python -m notice_ai.cli <command>

  setup-index [--recreate] [--pos-stoptags narrow|default]
                             Nori+kNN 인덱스 생성(이름은 NOTICE_INDEX, 기본 notices_v3)
  reindex --source 이름      기존 인덱스 문서를 NOTICE_INDEX 인덱스로 복사
  check-dict                사용자 사전이 필요한지·넣으면 나아지는지 확인(색인 안 건드림)
  collect [--pages N]        공지 수집 → 색인 → 새 공지 임베딩(--no-embed로 생략)
  ingest-csv 경로            사내 CSV 색인 → 새 공지 임베딩(--no-embed로 생략)
  embed                      임베딩 백필(벡터 검색용, Bedrock 필요)
  search <검색어> [--category 이름] [--hybrid] [--rerank] [--limit N]
  coins [검색어] [--limit N]  빗썸 거래 대상 목록 조회(캐시가 비어 있으면 한 번 받아 온다)
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

    pd = sub.add_parser("check-dict", help="사용자 사전 효과 확인(#34). 색인을 건드리지 않는다")
    pd.add_argument("--limit", type=int, default=0, help="검사할 코인 수(0=전체)")
    pd.add_argument("--offset", type=int, default=0, help="앞에서부터 건너뛸 수")

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

    pn = sub.add_parser("coins", help="빗썸 거래 대상 목록(티커·한글명) 조회·갱신")
    pn.add_argument("query", nargs="?", default="", help="티커·한글명·영문명 일부. 비우면 전체")
    pn.add_argument("--limit", type=int, default=30)

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
    elif a.command == "check-dict":
        from notice_ai.index_setup import diagnose_dictionary

        r = diagnose_dictionary(a.limit, offset=a.offset)
        if r["error"]:
            print(r["error"])
        else:
            print(f"사전 규칙 {r['rules']}개 · 코인 {r['checked']}/{r['total']}개 검사")
            print(f"  이름이 아예 안 남는 것 {r['missing']}개"
                  f" → 사전으로 고쳐짐 {r['fixed']}개 / 그대로 {r['still']}개")
            print(f"  (참고) 사전 때문에 조각 검색이 막히는 이름 {r['narrowed']}개")
            if r["fixed_examples"]:
                print("\n  고쳐지는 것:")
                for e in r["fixed_examples"]:
                    print(f"    {e['name']:<16} {' + '.join(e['now'])}  →  {' + '.join(e['after'])}")
            if r["still_examples"]:
                print("\n  사전을 넣어도 그대로:")
                for e in r["still_examples"]:
                    print(f"    {e['name']:<16} {' + '.join(e['now'])}  →  {' + '.join(e['after'])}")
            if r["narrowed_examples"]:
                print("\n  조각 검색이 막히는 것(득실 판단 필요):")
                for e in r["narrowed_examples"]:
                    print(f"    {e['name']:<16} {' + '.join(e['now'])}  →  {' + '.join(e['after'])}")

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
    elif a.command == "coins":
        from notice_ai import coins

        # CLI는 백그라운드 루프가 없으니(그건 api.py의 lifespan) 여기서 직접 한 번 받는다.
        r = coins.refresh()
        if not r["ok"]:
            print(f"빗썸 호출 실패 — {r['error']}")
        found = coins.search(a.query, limit=a.limit)
        print(f"전체 {r['count']}건 중 {len(found)}건")
        for c in found:
            marks = "/".join(c.markets) or "-"
            print(f"  {c.ticker:<10} {c.name or '(이름없음)':<16} {c.english:<24} {marks}"
                  + ("  ⚠유의" if c.warning else ""))
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
