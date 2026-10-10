#!/bin/sh
# Fetch res/schema.xml from Mixxx main and print every revision above our ceiling. Manual; uses the network.
set -eu
cd "$(dirname "$0")/.."
URL="${MIXXX_SCHEMA_URL:-https://raw.githubusercontent.com/mixxxdj/mixxx/main/res/schema.xml}"
CEILING="$(sed -n 's/^SCHEMA_CEILING = \([0-9]*\).*/\1/p' src/mixxxlib/db/schema.py)"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT
curl -fsSL "$URL" -o "$TMP"
echo "ceiling: $CEILING"
grep -o 'revision="[0-9]*"' "$TMP" | sed 's/[^0-9]//g' | sort -n | uniq | awk -v c="$CEILING" '$1 > c { print "upstream revision above ceiling: " $1; found = 1 } END { if (!found) print "no revisions above the ceiling" }'
