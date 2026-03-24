#!/usr/bin/env bash
set -euo pipefail

ROOT="/Users/jowang/Documents/github/silicon_agent"
export PATH="/opt/homebrew/bin:$PATH"

NPM_BIN="$(command -v npm || true)"
if [ -z "${NPM_BIN:-}" ] && [ -x "/opt/homebrew/bin/npm" ]; then
  NPM_BIN="/opt/homebrew/bin/npm"
fi

# cleanup old listeners
pid8000=$(lsof -tiTCP:8000 -sTCP:LISTEN -n -P || true)
if [ -n "${pid8000:-}" ]; then kill -9 $pid8000 || true; fi
pid3000=$(lsof -tiTCP:3000 -sTCP:LISTEN -n -P || true)
if [ -n "${pid3000:-}" ]; then kill -9 $pid3000 || true; fi

# start backend
cd "$ROOT/platform"
nohup ./.venv/bin/python -m uvicorn app.main:app --host 0.0.0.0 --port 8000 >/tmp/silicon_platform_8000.log 2>&1 &

# start frontend
cd "$ROOT/web"
nohup env NODE_OPTIONS=--dns-result-order=ipv4first "${NPM_BIN}" run dev -- --host 0.0.0.0 --port 3000 >/tmp/silicon_web_3000.log 2>&1 &

sleep 3

echo "== ports =="
lsof -nP -iTCP:8000 -sTCP:LISTEN || true
lsof -nP -iTCP:3000 -sTCP:LISTEN || true

echo "== probes =="
curl -sS -m 5 http://127.0.0.1:8000/health || true
echo
curl -sS -m 8 http://127.0.0.1:8000/api/v1/agents | head -c 300 || true
echo
curl -sS -I -m 5 http://127.0.0.1:3000 | sed -n '1,5p' || true
