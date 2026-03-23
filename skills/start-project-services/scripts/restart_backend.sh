#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/jowang/Documents/github/silicon_agent"

pid8000=$(lsof -tiTCP:8000 -sTCP:LISTEN -n -P || true)
if [ -n "${pid8000:-}" ]; then kill -9 $pid8000 || true; fi

cd "$ROOT/platform"
nohup ./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >/tmp/silicon_platform_8000.log 2>&1 &

sleep 1
lsof -nP -iTCP:8000 -sTCP:LISTEN || true
curl -sS -m 5 http://127.0.0.1:8000/health || true
echo
