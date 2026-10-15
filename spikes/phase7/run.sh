#!/bin/zsh
# Runs every image-search configuration in its own process (one model in memory at a time) and records the peak
# memory footprint reported by /usr/bin/time -l. Usage: spikes/phase7/run.sh [config ...]
set -uo pipefail
HERE="${0:A:h}"
PY="${HERE:h:h}/.venv/bin/python"
OUT="$HERE/data/bench"
mkdir -p "$OUT"

configs=(
  "eg2 mlx-community/embeddinggemma-2-8bit 70"
  "eg2 mlx-community/embeddinggemma-2-8bit 140"
  "eg2 mlx-community/embeddinggemma-2-8bit 280"
  "eg2 mlx-community/embeddinggemma-2-8bit 560"
  "eg2 mlx-community/embeddinggemma-2-4bit 140"
  "eg2 mlx-community/embeddinggemma-2-bf16 140"
  "siglip google/siglip2-base-patch16-256 fp32"
  "siglip google/siglip2-base-patch16-256 fp16"
  "siglip google/siglip2-large-patch16-256 fp16"
  "siglip google/siglip2-so400m-patch16-384 fp16"
)
[[ $# -gt 0 ]] && configs=("$@")

for cfg in "${configs[@]}"; do
  args=(${=cfg})
  log="$OUT/run.log"
  /usr/bin/time -l "$PY" "$HERE/image_bench.py" "${args[@]}" >"$log.tmp" 2>&1
  grep -vE "Warning|warn" "$log.tmp" | grep -E '^(eg2|siglip2)-' | tail -1
  tag=$(grep -E '^(eg2|siglip2)-' "$log.tmp" | tail -1 | cut -d' ' -f1)
  peak=$(grep "peak memory footprint" "$log.tmp" | awk '{print int($1/1048576)}')
  [[ -n "$tag" && -n "$peak" ]] && echo "$peak" > "$OUT/$tag.peak"
  cat "$log.tmp" >> "$log"
done
"$PY" "$HERE/image_bench.py" report
