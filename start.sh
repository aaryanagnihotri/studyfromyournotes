#!/usr/bin/env bash
set -e
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
  python3 -m venv .venv
fi
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -r requirements.txt
if [ ! -f ".env" ]; then
  cp .env.example .env
  # Keep the generated local config in demo mode.
  sed -i.bak 's/^APP_MODE=azure/# APP_MODE=azure/' .env 2>/dev/null || true
  sed -i.bak 's/^APP_MODE=demo/APP_MODE=demo/' .env 2>/dev/null || true
  rm -f .env.bak
fi
echo ""
echo "Starting Document Q&A Tutor in LOCAL DEMO mode (no real AI model)."
echo "Open http://127.0.0.1:8000"
python -m uvicorn app.main:app --host 127.0.0.1 --port 8000
