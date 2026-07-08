#!/usr/bin/env bash
set -euo pipefail
if [[ $# -lt 1 ]]; then
  echo "Usage: $0 /path/to/repo" >&2
  exit 2
fi
REPO="$1"
SRC_DIR="$(cd "$(dirname "$0")" && pwd)/.agents/skills"
DST_DIR="$REPO/.agents/skills"
mkdir -p "$DST_DIR"
cp -R "$SRC_DIR"/* "$DST_DIR"/
echo "Installed skills to $DST_DIR"
echo "Restart Codex if the skills do not appear."
