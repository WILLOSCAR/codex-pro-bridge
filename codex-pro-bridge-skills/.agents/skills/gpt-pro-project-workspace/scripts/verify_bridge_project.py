#!/usr/bin/env python3
"""Verify Bridge Project identity, audit chain, binding, sources, and tasks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import BridgeError  # noqa: E402
from project_store import BridgeProjectStore  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(description="Verify one Codex Pro Bridge Project.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--bridge-project-id", default="")
    parser.add_argument("--require-active-binding", action="store_true")
    parser.add_argument("--require-synced-sources", action="store_true")
    parser.add_argument("--require-inventory-verified", action="store_true")
    args = parser.parse_args()
    try:
        store = BridgeProjectStore(Path(args.repo))
        report = store.verify(args.bridge_project_id)
        if args.require_active_binding and not report["active_binding"]:
            raise BridgeError("Bridge Project does not have an active remote binding")
        if args.require_synced_sources and report["unsynced_source_count"]:
            raise BridgeError(
                f"Bridge Project has {report['unsynced_source_count']} unsynced sources"
            )
        if (
            args.require_inventory_verified
            and report["source_count"]
            and not report["inventory_verified"]
        ):
            raise BridgeError(
                "Bridge Project Sources have no verified remote inventory"
            )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (BridgeError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
