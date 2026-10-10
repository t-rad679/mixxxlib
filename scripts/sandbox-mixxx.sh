#!/bin/sh
# Live Mixxx harness (spec §12.3). Linux only, manual. Usage: scripts/sandbox-mixxx.sh TRACK_ID [extra mixxxlib args...]
# Copies the real database and mixxx.cfg into /tmp/mixxx-sandbox, applies a test write with mixxxlib,
# launches Mixxx against the sandbox, screenshots it, stops it, and diffs the database against the pre-launch snapshot.
set -eu
TRACK="${1:?track id}"
shift
SANDBOX=/tmp/mixxx-sandbox
SRC="${MIXXX_SETTINGS:-$HOME/.mixxx}"
MIXXXLIB="${MIXXXLIB:-$(dirname "$0")/../.venv/bin/mixxxlib}"
if pgrep -x mixxx >/dev/null; then echo "Mixxx is running; close it first" >&2; exit 5; fi
rm -rf "$SANDBOX"
mkdir -p "$SANDBOX/backups"
cp "$SRC/mixxxdb.sqlite" "$SANDBOX/"
cp "$SRC/mixxx.cfg" "$SANDBOX/"
"$MIXXXLIB" --settings-path "$SANDBOX" --backup-dir "$SANDBOX/backups" cue add "$TRACK" --type hotcue --slot 7 --position 1.0 --color '#C50A08' --label mixxxlib "$@"
sqlite3 "$SANDBOX/mixxxdb.sqlite" ".dump cues" > "$SANDBOX/cues-before.sql"
sqlite3 "$SANDBOX/mixxxdb.sqlite" "select * from sqlite_sequence" > "$SANDBOX/sequence-before.txt"
QT_QPA_PLATFORM=xcb mixxx --settings-path "$SANDBOX" --safe-mode "$(sqlite3 "$SANDBOX/mixxxdb.sqlite" "select tl.location from library l join track_locations tl on tl.id=l.location where l.id=$TRACK")" > "$SANDBOX/mixxx.out" 2>&1 &
MIXXX_PID=$!
sleep 20
WID="$(xdotool search --name 'Mixxx' | head -1 || true)"
[ -n "$WID" ] && import -window "$WID" "$SANDBOX/screenshot.png" || echo "no Mixxx window found for screenshot" >&2
kill -TERM "$MIXXX_PID" || true
wait "$MIXXX_PID" 2>/dev/null || true
sqlite3 "$SANDBOX/mixxxdb.sqlite" ".dump cues" > "$SANDBOX/cues-after.sql"
sqlite3 "$SANDBOX/mixxxdb.sqlite" "select * from sqlite_sequence" > "$SANDBOX/sequence-after.txt"
echo "--- cues diff (empty means Mixxx kept the rows byte-identical)"
diff "$SANDBOX/cues-before.sql" "$SANDBOX/cues-after.sql" || true
echo "--- sqlite_sequence diff (one new Playlists history row per launch is expected noise)"
diff "$SANDBOX/sequence-before.txt" "$SANDBOX/sequence-after.txt" || true
echo "screenshot: $SANDBOX/screenshot.png  log: $SANDBOX/mixxx.out"
