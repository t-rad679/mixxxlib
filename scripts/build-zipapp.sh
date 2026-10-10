#!/bin/sh
# Build the single-file zipapp: dist/mixxxlib (or $1). Needs the dev venv (shiv) on PATH or .venv/bin.
set -eu
cd "$(dirname "$0")/.."
OUT="${1:-dist/mixxxlib}"
if [ -x .venv/bin/shiv ]; then SHIV=.venv/bin/shiv; else SHIV=shiv; fi
mkdir -p "$(dirname "$OUT")"
"$SHIV" -c mixxxlib -o "$OUT" -p '/usr/bin/env python3' .
echo "built $OUT"
