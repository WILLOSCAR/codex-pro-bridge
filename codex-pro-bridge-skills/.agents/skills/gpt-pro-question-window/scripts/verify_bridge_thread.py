#!/usr/bin/env python3
"""Verify a Codex Pro Bridge thread and every referenced local artifact."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import BridgeError, verify_thread_integrity  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify parent links, ordering, artifact hashes, and bundle hashes."
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--bridge-thread-id", required=True)
    parser.add_argument("--require-complete-rounds", action="store_true")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()
    try:
        report = verify_thread_integrity(
            Path(args.repo),
            args.bridge_thread_id,
            require_complete_rounds=args.require_complete_rounds,
        )
        if args.json:
            print(json.dumps(report, ensure_ascii=False, sort_keys=True))
        else:
            print(
                "Verified bridge thread {thread_id}: {event_count} events, "
                "{complete_rounds} complete rounds, {artifact_count} artifacts, "
                "{bundle_count} bundles.".format(**report)
            )
        return 0
    except (BridgeError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
