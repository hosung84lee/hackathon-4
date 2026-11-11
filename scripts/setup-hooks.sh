#!/bin/sh
# 사용법: sh scripts/setup-hooks.sh @본인토큰
#   본인토큰은 .github/CODEOWNERS 에 적힌 값 (예: @OWNER_A)
set -e
ROOT=$(git rev-parse --show-toplevel)
cd "$ROOT"
git config core.hooksPath .githooks
chmod +x .githooks/* 2>/dev/null || true
if [ -n "$1" ]; then
  if ! grep -q -- "$1" .github/CODEOWNERS; then
    echo "경고: '$1' 이 .github/CODEOWNERS 에 없습니다." >&2
  fi
  git config hackathon.owner "$1"
fi
echo "hooksPath = $(git config core.hooksPath)"
echo "owner     = $(git config hackathon.owner || echo '(미설정)')"
