#!/bin/bash
# Rebuilds the Amy fine-tuning dataset from all Claude Code session transcripts.
# Runs automatically via launchd — see com.anthos.amy-dataset.plist

set -e

REPO="$HOME/ava-companion"
VENV="$REPO/venv"
PROJECT="$HOME/.claude/projects/-Users-anthosintelligence-ava-companion"
OUT="$REPO/data/amy_finetune_all.jsonl"
LOG="$REPO/logs/dataset_update.log"

mkdir -p "$REPO/logs"
echo "$(date '+%Y-%m-%d %H:%M:%S') running dataset update" >> "$LOG"

source "$VENV/bin/activate"
python3 "$REPO/scripts/transcript_to_dataset.py" \
    --project "$PROJECT" \
    --format chat \
    --out "$OUT" >> "$LOG" 2>&1

echo "$(date '+%Y-%m-%d %H:%M:%S') done — $(wc -l < "$OUT") examples" >> "$LOG"
