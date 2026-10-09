#!/usr/bin/env bash
# daily_collect.sh — 하루 1회 자동 증분 수집. Windows 작업 스케줄러가 Git Bash로 부른다.
# ─────────────────────────────────────────────────────────────────────────────
# env.sh(비밀값)를 불러와 전 카테고리 증분 수집 + 자동 임베딩을 돌리고 scripts/logs/ 에 남긴다.
# 증분이라 각 카테고리는 '이미 있는 페이지'를 만나면 멈춘다 → 매일 돌려도 가볍고 429도 덜 함.
# 과거 이력의 구멍은 증분으로 안 메워진다(그건 `collect --category <이름>` 전량으로 따로).
# 등록 방법은 docs/LOCAL_TESTING.md "자동화" 참고.
# ─────────────────────────────────────────────────────────────────────────────
here="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
repo="$(cd "$here/.." && pwd)"
cd "$repo"

if [ ! -f "$here/env.sh" ]; then
    echo "scripts/env.sh 이 없습니다. cp scripts/env.sh.example scripts/env.sh 후 값 채우기" >&2
    exit 1
fi
source "$here/env.sh" >/dev/null

mkdir -p "$here/logs"
log="$here/logs/daily_collect.log"

echo "===== $(date '+%F %T') 증분 수집 시작 =====" >> "$log"
py -m notice_ai.cli collect --incremental --gap 4 >> "$log" 2>&1
status=$?
echo "===== $(date '+%F %T') 종료(exit=$status) =====" >> "$log"
echo >> "$log"
exit $status
