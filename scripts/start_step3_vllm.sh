#!/usr/bin/env bash
set -euo pipefail

focus_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
focus_repo_dir="$(cd "$focus_script_dir/.." && pwd)"
focus_vllm="$focus_repo_dir/.vllm-venv/bin/vllm"
focus_model="$focus_repo_dir/.models/Step3-VL-10B-FP8"

if [[ ! -x "$focus_vllm" ]]; then
  echo "vLLM environment is missing: $focus_vllm" >&2
  exit 1
fi
if [[ ! -f "$focus_model/config.json" || ! -f "$focus_model/model-00005.safetensors" ]]; then
  echo "Step3-VL model download is incomplete: $focus_model" >&2
  exit 1
fi

exec "$focus_vllm" serve "$focus_model" \
  --served-model-name step3-vl-focus \
  --host 127.0.0.1 \
  --port 8040 \
  --trust-remote-code \
  --reasoning-parser deepseek_r1 \
  --dtype auto \
  --max-model-len 4096 \
  --max-num-seqs 1 \
  --limit-mm-per-prompt '{"image":4}' \
  --gpu-memory-utilization 0.30
