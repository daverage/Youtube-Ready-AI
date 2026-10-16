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

NEEDS_INSTALL=0
if ! python -c "import fastapi, uvicorn, PIL, cv2" >/dev/null 2>&1; then
    NEEDS_INSTALL=1
fi

EXTRAS=".[web,thumbnails]"
if [ "$(uname -s)" = "Darwin" ] && [ "$(uname -m)" = "arm64" ]; then
    EXTRAS=".[web,thumbnails,mlx]"
    if ! python -c "import importlib.util; raise SystemExit(0 if importlib.util.find_spec('mlx_lm') and importlib.util.find_spec('mlx_vlm') else 1)" >/dev/null 2>&1; then
        NEEDS_INSTALL=1
    fi
fi

if [ "$NEEDS_INSTALL" -ne 0 ]; then
    echo "Installing dependencies..."
    pip install --quiet --upgrade pip
    pip install --quiet -e "$EXTRAS"
fi

echo "Starting youtube-ready-ai..."
exec youtube-ready-web "$@"
