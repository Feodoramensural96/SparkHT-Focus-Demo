#!/usr/bin/env bash
set -euo pipefail

focus_check_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
focus_repo_dir="$(cd "$focus_check_dir/.." && pwd)"
focus_mode="gateway"

if [[ ${1:-} == "--full" ]]; then
  focus_mode="full"
elif [[ $# -gt 0 ]]; then
  echo "Usage: $0 [--full]" >&2
  exit 2
fi

if ! command -v curl >/dev/null 2>&1; then
  echo "[FAIL] curl is required" >&2
  exit 1
fi

focus_failures=0

focus_probe() {
  local focus_label="$1"
  local focus_url="$2"
  if curl --fail --silent --show-error --max-time 5 \
    --output /dev/null "$focus_url"; then
    printf '[ OK ] %-14s %s\n' "$focus_label" "$focus_url"
  else
    printf '[FAIL] %-14s %s\n' "$focus_label" "$focus_url" >&2
    focus_failures=$((focus_failures + 1))
  fi
}

focus_probe "Focus gateway" "http://127.0.0.1:8780/health"

if [[ "$focus_mode" == "full" ]]; then
  focus_probe "Qwen ASR" "http://127.0.0.1:8010/health"
  focus_probe "Ollama" "http://127.0.0.1:11434/api/tags"
  focus_probe "Qwen3-TTS" "http://127.0.0.1:8030/health"
  focus_probe "Step3-VL" "http://127.0.0.1:8040/v1/models"
fi

if ((focus_failures > 0)); then
  printf '%d required service(s) are unavailable.\n' "$focus_failures" >&2
  exit 1
fi

if [[ "$focus_mode" == "full" ]]; then
  focus_python="$focus_repo_dir/.venv/bin/python"
  if [[ ! -x "$focus_python" ]]; then
    echo "[FAIL] gateway environment is missing: $focus_python" >&2
    exit 1
  fi
  echo "Running one real ASR -> Ollama -> TTS request..."
  "$focus_python" "$focus_check_dir/benchmark_fast_chain.py" --runs 1
fi

echo "Focus service preflight passed ($focus_mode mode)."
