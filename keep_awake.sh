#!/bin/bash
# Keep Mac awake while Amy runs overnight jobs or long thought loops.
# Uses macOS caffeinate — built in, no install needed.
# Usage: ./keep_awake.sh [hours]   default: 3 hours

HOURS="${1:-3}"
SECONDS_VAL=$((HOURS * 3600))

# Check if already running
if pgrep -f "caffeinate.*keep_awake" > /dev/null 2>&1; then
  echo "[keep_awake] Already running. Kill it first: pkill -f 'caffeinate.*keep_awake'"
  exit 1
fi

echo "[keep_awake] Keeping Mac awake for ${HOURS}h (${SECONDS_VAL}s) — display + system"
echo "[keep_awake] Stop early: pkill -f 'caffeinate.*keep_awake'"

# -d: prevent display sleep, -i: prevent idle sleep, -s: prevent system sleep
caffeinate -dis -t "$SECONDS_VAL" &
PID=$!
echo "[keep_awake] caffeinate PID: $PID"
wait $PID
echo "[keep_awake] Done — system can sleep again."
