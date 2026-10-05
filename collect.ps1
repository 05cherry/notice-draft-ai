# collect.ps1 — 환경변수(env.local.ps1)를 불러온 뒤 공지 수집을 실행하는 래퍼 (PowerShell)
# ─────────────────────────────────────────────────────────────────────────────
# 수집 전용 터미널에서 이 한 줄만 치면 된다 — env 로드와 수집이 같은 프로세스에서 끝난다.
# (환경변수는 프로세스마다 따로라, 로드와 실행이 같은 셸 안에서 이어져야 하기 때문)
#
#   .\collect.ps1 --incremental --no-embed
#   .\collect.ps1 --category 입출금 --max-pages 1 --no-body --no-embed
#
# 뒤에 붙인 인자는 그대로 `py -m notice_ai.cli collect` 로 넘어간다.
# env.local.ps1 은 env.local.ps1.example 를 복사해 값을 채운 파일(비밀값이라 커밋 안 됨).
# 실행 정책에 막히면:  Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
# ─────────────────────────────────────────────────────────────────────────────

$ErrorActionPreference = "Stop"
$envFile = Join-Path $PSScriptRoot "env.local.ps1"

if (-not (Test-Path $envFile)) {
    Write-Host "env.local.ps1 이 없습니다. 먼저 템플릿을 복사해 값을 채우세요:" -ForegroundColor Yellow
    Write-Host "  Copy-Item env.local.ps1.example env.local.ps1" -ForegroundColor Yellow
    exit 1
}

. $envFile                          # 이 프로세스에 환경변수 심기(끝에 '로드 완료' 한 줄 찍힘)
py -m notice_ai.cli collect @args   # 뒤에 붙인 인자(--incremental 등)를 그대로 넘김
exit $LASTEXITCODE
