#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "$0")" && pwd)"
SRC_DIR="$ROOT_DIR/.agents/skills"

usage() {
  echo "Usage: $0 --global | --repo /path/to/repo" >&2
  exit 2
}

case "${1:-}" in
  --global)
    [[ $# -eq 1 ]] || usage
    DST_DIR="${CODEX_HOME:-$HOME/.codex}/skills"
    ;;
  --repo)
    [[ $# -eq 2 ]] || usage
    [[ -d "$2" ]] || { echo "Repository directory does not exist: $2" >&2; exit 2; }
    REPO_ROOT="$(cd "$2" && pwd)"
    DST_DIR="$REPO_ROOT/.agents/skills"
    ;;
  *) usage ;;
esac

[[ -d "$SRC_DIR" ]] || { echo "Missing source skills directory: $SRC_DIR" >&2; exit 2; }
mkdir -p "$DST_DIR"
STAGE_DIR="$(mktemp -d "$DST_DIR/.codex-pro-bridge-install.XXXXXX")"
trap 'rm -rf "$STAGE_DIR"' EXIT

managed=()
for source_path in "$SRC_DIR"/* "$SRC_DIR"/.shared; do
  [[ -e "$source_path" ]] || continue
  name="$(basename "$source_path")"
  managed+=("$name")
  cp -R "$source_path" "$STAGE_DIR/$name"
done

for name in "${managed[@]}"; do
  case "$name" in
    .shared|bundle-algorithm-context|experiment-plan-generator|gpt-pro-algorithm-pipeline|gpt-pro-paper-brainstormer|gpt-pro-question-window|gpt-pro-research-algorithm-reviewer|implementation-consistency-checker) ;;
    *) echo "Refusing unexpected managed entry: $name" >&2; exit 2 ;;
  esac
  rm -rf "$DST_DIR/$name"
  mv "$STAGE_DIR/$name" "$DST_DIR/$name"
done

rmdir "$STAGE_DIR"
trap - EXIT
printf 'Installed %s\n' "${managed[@]}"
echo "Destination: $DST_DIR"
if [[ -n "${REPO_ROOT:-}" ]] && git -C "$REPO_ROOT" rev-parse --git-dir >/dev/null 2>&1; then
  GIT_DIR="$(git -C "$REPO_ROOT" rev-parse --git-dir)"
  [[ "$GIT_DIR" = /* ]] || GIT_DIR="$REPO_ROOT/$GIT_DIR"
  EXCLUDE_FILE="$GIT_DIR/info/exclude"
  mkdir -p "$(dirname "$EXCLUDE_FILE")"
  touch "$EXCLUDE_FILE"
  for pattern in .agents/ .codex/; do
    grep -Fqx "$pattern" "$EXCLUDE_FILE" || echo "$pattern" >> "$EXCLUDE_FILE"
  done
  echo "Local Git exclude updated for .agents/ and .codex/."
fi
echo "Restart Codex if the updated skills do not appear in an existing session."
