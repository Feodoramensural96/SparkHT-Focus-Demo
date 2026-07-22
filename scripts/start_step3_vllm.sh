#!/usr/bin/env bash
set -euo pipefail

focus_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
focus_repo_dir="$(cd "$focus_script_dir/.." && pwd)"
focus_vllm="$focus_repo_dir/.vllm-venv/bin/vllm"
focus_model="$focus_repo_dir/.models/Step3-VL-10B-FP8"
focus_verify="$focus_script_dir/verify_step3_model.sh"

if [[ ! -x "$focus_vllm" ]]; then
  echo "vLLM environment is missing: $focus_vllm" >&2
  exit 1
fi
if [[ ! -x "$focus_verify" ]]; then
  echo "Step3-VL verifier is missing or not executable: $focus_verify" >&2
  exit 1
fi

"$focus_verify"

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
