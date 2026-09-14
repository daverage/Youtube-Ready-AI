#!/usr/bin/env bash
# One-click launcher for the youtube-ready-ai web UI (macOS / Linux).
set -euo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")"

if ! command -v python3 >/dev/null 2>&1; then
    echo "Python 3.11+ is required but was not found on this machine." >&2
    echo "Install it from https://www.python.org/downloads/ and run this script again." >&2
    exit 1
fi

if [ ! -d ".venv" ]; then
    echo "Setting up youtube-ready-ai for the first time (this only happens once)..."
    python3 -m venv .venv
fi

# shellcheck disable=SC1091
source .venv/bin/activate

if ! python -c "import fastapi, uvicorn" >/dev/null 2>&1; then
    echo "Installing dependencies..."
    pip install --quiet --upgrade pip
    pip install --quiet -e ".[web]"
fi

echo "Starting youtube-ready-ai..."
exec youtube-ready-web "$@"
