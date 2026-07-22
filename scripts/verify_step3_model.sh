#!/usr/bin/env bash
set -euo pipefail

focus_script_dir="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
focus_repo_dir="$(cd "$focus_script_dir/.." && pwd)"
focus_model="$focus_repo_dir/.models/Step3-VL-10B-FP8"
focus_checksums="$focus_script_dir/step3-vl-10b-fp8.sha256"

focus_required_files=(
  added_tokens.json
  chat_template.jinja
  config.json
  configuration_step_vl.py
  generation_config.json
  model-00001.safetensors
  model-00002.safetensors
  model-00003.safetensors
  model-00004.safetensors
  model-00005.safetensors
  modeling_step_vl.py
  processing_step3.py
  processor_config.json
  special_tokens_map.json
  tokenizer.json
  tokenizer_config.json
  vision_encoder.py
  vocab.json
)

focus_missing=()
for focus_name in "${focus_required_files[@]}"; do
  if [[ ! -s "$focus_model/$focus_name" ]]; then
    focus_missing+=("$focus_name")
  fi
done

if (( ${#focus_missing[@]} > 0 )); then
  echo "Step3-VL model is incomplete: $focus_model" >&2
  printf 'missing or empty: %s\n' "${focus_missing[@]}" >&2
  exit 1
fi

if [[ ! -f "$focus_checksums" ]]; then
  echo "Step3-VL checksum manifest is missing: $focus_checksums" >&2
  exit 1
fi

if ! (cd "$focus_repo_dir" && sha256sum --quiet -c "$focus_checksums"); then
  echo "Step3-VL weight checksum verification failed" >&2
  exit 1
fi

echo "Step3-VL model files and all five weight checksums are valid."
