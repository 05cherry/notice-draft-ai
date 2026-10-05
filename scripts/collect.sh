#!/usr/bin/env bash
# collect.sh — env.sh 를 불러온 뒤 공지 수집을 실행하는 래퍼 (Git Bash / bash)
# ─────────────────────────────────────────────────────────────────────────────
# 환경변수는 프로세스(터미널)마다 따로다. env 넣는 터미널과 수집 터미널을 나누면 수집 쪽이
# 비어 ConfigError 가 난다 — 로드와 실행이 같은 셸 안에서 이어져야 한다. 이 래퍼가 그 둘을
# 한 프로세스에 묶는다. 수집 전용 터미널에서 이 한 줄만:
#
#   scripts/collect.sh --incremental
#   scripts/collect.sh --category 점검 --max-pages 1 --no-body --no-embed
#
# 뒤에 붙인 인자는 그대로 `py -m notice_ai.cli collect` 로 넘어간다.
# scripts/env.sh 는 scripts/env.sh.example 를 복사해 값을 채운 파일(비밀값이라 커밋 안 됨).
# ─────────────────────────────────────────────────────────────────────────────
set -e

here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"
env_file="$here/env.sh"

if [ ! -f "$env_file" ]; then
    echo "scripts/env.sh 이 없습니다. 먼저 템플릿을 복사해 값을 채우세요:" >&2
    echo "  cp scripts/env.sh.example scripts/env.sh" >&2
    exit 1
fi

cd "$repo"              # PYTHONPATH=src 가 저장소 루트 기준으로 풀리도록
source "$env_file"      # 이 프로세스에 환경변수 심기(끝에 '로드 완료' 한 줄 찍힘)
py -m notice_ai.cli collect "$@"
