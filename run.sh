#!/usr/bin/env bash
# One command to start spot2am. Prefers an isolated venv; if venv is unavailable
# on this machine, it falls back to a local .deps folder so it still just works.
set -euo pipefail
cd "$(dirname "$0")"

if python3 -m venv .venv 2>/dev/null \
   && .venv/bin/python -c "import sys" 2>/dev/null \
   && .venv/bin/python -m pip install -q -r requirements.txt 2>/dev/null; then
  exec .venv/bin/python app.py
fi

echo "Note: venv unavailable here — installing locally into .deps instead."
rm -rf .venv
( python3 -m pip install -q --target=.deps -r requirements.txt \
  || pip3 install -q --target=.deps -r requirements.txt )
PYTHONPATH="$PWD/.deps${PYTHONPATH:+:$PYTHONPATH}" exec python3 app.py
