#!/bin/bash
# Indexes and evaluates prepared benchmark folders one after another (one model process at a time):
#   spikes/phase17/run_bench.sh <folder from beir_prepare.py>:<en|pl> ...
# Each folder gets home/ (its own isolated index) and eval.jsonl (fusion_eval.py's results and significance).
set -u
cd "$(dirname "$0")/../.."
for item in "$@"; do
  dir=${item%:*}; lang=${item#*:}
  mkdir -p "$dir/home"
  if [ ! -s "$dir/home/index.db" ] || ! grep -q indexed "$dir/index.log" 2>/dev/null; then
    LANTERN_HOME="$dir/home" LANTERN_SOCKET="$dir/none.sock" .venv/bin/lantern index "$dir/corpus" --no-transcribe > "$dir/index.log" 2>&1
  fi
  LANTERN_SOCKET="$dir/none.sock" .venv/bin/python spikes/phase17/fusion_eval.py "$dir/home" "$dir/queries.jsonl" "$dir/qrels" "$lang" \
    > "$dir/eval.jsonl" 2> "$dir/eval.err"
  echo "$item done $(date +%H:%M)"
done
