"""OpenSearch 연결.

AWS OpenSearch를 FGAC(내부 사용자 DB)로 만들었으므로 아이디/비번 basic auth로 붙는다.
IAM(SigV4) 방식이 아니라 단순해서 학습에 적합하다.
"""

from __future__ import annotations

from functools import lru_cache
from urllib.parse import urlparse

from opensearchpy import OpenSearch

from notice_ai import config


@lru_cache(maxsize=1)
def get_client() -> OpenSearch:
    parsed = urlparse(config.endpoint())
    host = parsed.hostname
    port = parsed.port or (443 if parsed.scheme == "https" else 80)
    return OpenSearch(
        hosts=[{"host": host, "port": port}],
        http_auth=config.basic_auth(),
        use_ssl=(parsed.scheme == "https"),
        verify_certs=True,
        ssl_show_warn=False,
        timeout=30,
        max_retries=3,
        retry_on_timeout=True,
    )
