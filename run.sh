#!/usr/bin/env bash
set -euo pipefail
cd "$(dirname "$0")"

if [ ! -d .venv ]; then
  python3 -m venv .venv
  ./.venv/bin/pip install -U pip
  ./.venv/bin/pip install -e .
fi

mkdir -p data/logs data/user_files

exec ./.venv/bin/uvicorn app.main:app --host 127.0.0.1 --port 8088 "$@"
