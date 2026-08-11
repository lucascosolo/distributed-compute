#!/usr/bin/env bash
# Operator-owned native baseline wrapper for `aipool compare`.
# Reads one rendered task context from stdin and returns only Codex's final text.
set -euo pipefail

output="$(mktemp)"
trap 'rm -f "$output"' EXIT

codex exec \
  --ephemeral \
  --sandbox read-only \
  --skip-git-repo-check \
  --output-last-message "$output" \
  - < <(cat)

cat "$output"
