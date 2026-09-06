"""HyDE — 검색 리콜용 가짜 공지 초안(선택). Claude on Bedrock.

BEDROCK_LLM_MODEL이 없으면 그냥 원문을 반환(HyDE 미사용). 최종 결과로는 안 쓴다.
"""

from __future__ import annotations

import json
import os
from functools import lru_cache

from notice_ai import config

_PROMPT = (
    "너는 가상자산 거래소 공지 작성자다. 아래 정보로 실제 공지처럼 보이는 "
    "짧은 초안(제목 1줄 + 본문 2~3문장)을 써라. 검색용 예시이니 없는 사실은 "
    "추가하지 말고 간결히.\n\n카테고리: {category}\n정보: {info}\n"
)


@lru_cache(maxsize=1)
def _client():
    import boto3

    return boto3.client("bedrock-runtime", region_name=config.AWS_REGION)


def hypothetical_notice(category_name: str, info_text: str) -> str:
    mid = os.environ.get("BEDROCK_LLM_MODEL")
    if not mid:
        return info_text
    body = json.dumps(
        {
            "anthropic_version": "bedrock-2023-05-31",
            "max_tokens": 300,
            "messages": [
                {"role": "user", "content": _PROMPT.format(category=category_name, info=info_text)}
            ],
        }
    )
    try:
        resp = _client().invoke_model(modelId=mid, body=body)
        return json.loads(resp["body"].read())["content"][0]["text"].strip()
    except Exception:
        return info_text
