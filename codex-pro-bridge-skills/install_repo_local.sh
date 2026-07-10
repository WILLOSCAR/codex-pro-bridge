#!/usr/bin/env bash
set -euo pipefail
[[ $# -eq 1 ]] || { echo "Usage: $0 /path/to/repo" >&2; exit 2; }
exec "$(cd "$(dirname "$0")" && pwd)/install.sh" --repo "$1"
