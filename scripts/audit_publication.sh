#!/usr/bin/env bash
set -euo pipefail

candidate_file="$(mktemp -t stackchan-publication.XXXXXX)"
trap 'rm -f "$candidate_file"' EXIT

git ls-files --cached --others --exclude-standard | sort -u > "$candidate_file"
candidate_count="$(wc -l < "$candidate_file" | tr -d ' ')"
candidate_bytes="$(
  while IFS= read -r path; do
    [[ -f "$path" ]] && stat -f '%z' "$path"
  done < "$candidate_file" | awk '{ total += $1 } END { print total + 0 }'
)"

printf 'Publication candidates: %s files, %s bytes\n' "$candidate_count" "$candidate_bytes"

forbidden_path_pattern='(^|/)(artifacts|server/data|secrets|node_modules|\.pixi|\.venv|\.eve|\.output|\.pio)(/|$)|(^|/)(DeviceSecret|LocalConfig)\.hpp$|\.(bin|elf|map|log|jsonl|wav|pcm|sqlite3)(-|$|\.)'
forbidden_paths="$(grep -E "$forbidden_path_pattern" "$candidate_file" || true)"
private_env_paths="$(grep -E '(^|/)\.env($|\.)' "$candidate_file" | grep -Ev '(^|/)\.env\.example$' || true)"
if [[ -n "$private_env_paths" ]]; then
  forbidden_paths="${forbidden_paths}${forbidden_paths:+$'\n'}${private_env_paths}"
fi
if [[ -n "$forbidden_paths" ]]; then
  printf 'Forbidden publication paths found:\n%s\n' "$forbidden_paths" >&2
  exit 1
fi

oversized="$(
  while IFS= read -r path; do
    [[ -f "$path" ]] || continue
    size="$(stat -f '%z' "$path")"
    (( size > 10000000 )) && printf '%s\n' "$path"
  done < "$candidate_file"
  true
)"
if [[ -n "$oversized" ]]; then
  printf 'Unexpected files larger than 10 MB:\n%s\n' "$oversized" >&2
  exit 1
fi

secret_pattern='AKIA[0-9A-Z]{16}|AIza[0-9A-Za-z_-]{35}|gh[pousr]_[0-9A-Za-z]{20,}|sk-[A-Za-z0-9_-]{20,}|-----BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY-----'
secret_files="$(
  while IFS= read -r path; do
    [[ -f "$path" ]] || continue
    rg -l --no-messages -e "$secret_pattern" -- "$path" || true
  done < "$candidate_file" | sort -u
)"
if [[ -n "$secret_files" ]]; then
  printf 'Possible credentials found in:\n%s\n' "$secret_files" >&2
  exit 1
fi

printf 'Publication boundary and high-confidence credential scan passed.\n'
