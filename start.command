#!/bin/zsh
set -e

cd "${0:A:h}"
export PATH="/opt/homebrew/bin:$PATH"
exec "${STUDIO_PYTHON:-/opt/anaconda3/bin/python}" -m uvicorn api.main:create_app --factory --host 127.0.0.1 --port 8765 "$@"