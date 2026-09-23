#!/usr/bin/env python3
"""Stage, verify, or clean one digest-bound browser upload file."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import BridgeError
from browser_host import cleanup_staged_file, stage_browser_file, verify_staged_file


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    stage = sub.add_parser("stage")
    stage.add_argument("--source", required=True)
    stage.add_argument("--bridge-thread-id", required=True)
    verify = sub.add_parser("verify")
    verify.add_argument("--staged-path", required=True)
    verify.add_argument("--source", default="")
    verify.add_argument("--expected-sha256", default="")
    verify.add_argument("--expected-browser-path", default="")
    cleanup = sub.add_parser("cleanup")
    cleanup.add_argument("--staged-path", required=True)
    cleanup.add_argument("--expected-sha256", required=True)
    args = parser.parse_args()
    try:
        if args.command == "stage":
            result = stage_browser_file(args.source, thread_id=args.bridge_thread_id)
        elif args.command == "verify":
            result = verify_staged_file(
                args.staged_path,
                source_path=args.source or None,
                expected_sha256=args.expected_sha256,
                expected_browser_path=args.expected_browser_path,
            )
        else:
            result = cleanup_staged_file(
                args.staged_path, expected_sha256=args.expected_sha256
            )
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (BridgeError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
