#!/usr/bin/env python3
"""Reconcile browser-observed Project Sources with the local manifest."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import BridgeError  # noqa: E402
from source_manifest import ProjectSourceManager  # noqa: E402


def resolve_cli_path(value: str, repo: Path) -> Path:
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (repo / path).resolve()


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Record a complete read-only observation of ChatGPT Project Sources."
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--bridge-project-id", default="")
    parser.add_argument("--remote-inventory-file", required=True)
    args = parser.parse_args()
    try:
        repo = Path(args.repo).expanduser().resolve()
        manager = ProjectSourceManager(repo)
        project_id = manager.store.resolve_project_id(args.bridge_project_id)
        inventory = json.loads(
            resolve_cli_path(args.remote_inventory_file, repo).read_text(
                encoding="utf-8"
            )
        )
        report = manager.reconcile_remote_inventory(
            project_id,
            remote_inventory=inventory,
        )
        print(json.dumps(report, ensure_ascii=False, indent=2, sort_keys=True))
        return 0 if report["valid"] else 2
    except (BridgeError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
