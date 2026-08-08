#!/usr/bin/env bash
set -euo pipefail

model_dir="${STACKCHAN_MODEL_DIR:-artifacts/models}"
base_url="https://huggingface.co/ggerganov/whisper.cpp/resolve/main"
mkdir -p "$model_dir"

download_model() {
  local name="$1"
  local expected_sha256="$2"
  local destination="$model_dir/$name"
  local temporary="$destination.part"

  if [[ -f "$destination" ]] && echo "$expected_sha256  $destination" | shasum -a 256 -c - >/dev/null 2>&1; then
    printf 'Verified existing %s\n' "$name"
    return
  fi

  rm -f "$temporary"
  curl --fail --location --retry 3 --output "$temporary" "$base_url/$name"
  echo "$expected_sha256  $temporary" | shasum -a 256 -c -
  mv "$temporary" "$destination"
  printf 'Installed %s\n' "$destination"
}

download_model \
  "ggml-base-q5_1.bin" \
  "422f1ae452ade6f30a004d7e5c6a43195e4433bc370bf23fac9cc591f01a8898"
download_model \
  "ggml-small-q5_1.bin" \
  "ae85e4a935d7a567bd102fe55afc16bb595bdb618e11b2fc7591bc08120411bb"
download_model \
  "ggml-large-v3-turbo-q5_0.bin" \
  "394221709cd5ad1f40c46e6031ca61bce88931e6e088c188294c6d5a55ffa7e2"
