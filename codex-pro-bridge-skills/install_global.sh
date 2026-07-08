#!/usr/bin/env bash
set -euo pipefail
SRC_DIR="$(cd "$(dirname "$0")" && pwd)/.agents/skills"
DST_DIR="${CODEX_HOME:-$HOME/.codex}/skills"
mkdir -p "$DST_DIR"
cp -R "$SRC_DIR"/* "$DST_DIR"/
echo "Installed skills to $DST_DIR"
echo "Restart Codex if the skills do not appear."
