#!/usr/bin/env python3
"""Record browser-observed Project Source effects against one immutable plan."""

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
        description="Record visually verified ChatGPT Project Source state."
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--bridge-project-id", default="")
    parser.add_argument("--plan", required=True)
    parser.add_argument("--present", action="append", default=[])
    parser.add_argument("--removed", action="append", default=[])
    parser.add_argument("--failed", action="append", default=[])
    parser.add_argument("--remote-inventory-file", default="")
    parser.add_argument(
        "--assume-plan-complete",
        action="store_true",
        help="Use only after every desired filename is visibly present.",
    )
    args = parser.parse_args()
    try:
        repo = Path(args.repo).expanduser().resolve()
        manager = ProjectSourceManager(repo)
        project_id = manager.store.resolve_project_id(args.bridge_project_id)
        plan_path = resolve_cli_path(args.plan, repo)
        plan = json.loads(plan_path.read_text(encoding="utf-8"))
        present = list(args.present)
        removed = list(args.removed)
        if args.assume_plan_complete:
            if not args.remote_inventory_file:
                raise BridgeError(
                    "--remote-inventory-file is required with --assume-plan-complete"
                )
            present = [
                str(item["remote_name"]) for item in plan.get("desired_sources", [])
            ]
            removed = [
                str(item["name"]) for item in plan.get("removals_after_upload", [])
            ]
        if args.remote_inventory_file:
            remote_inventory = json.loads(
                resolve_cli_path(args.remote_inventory_file, repo).read_text(
                    encoding="utf-8"
                )
            )
        else:
            remote_inventory = plan.get("remote_inventory", [])
        observation = {
            "present": present,
            "removed": removed,
            "failed": args.failed,
            "remote_inventory": remote_inventory,
        }
        manifest = manager.record(
            project_id, plan_path=plan_path, observation=observation
        )
        print(json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True))
        return 0
    except (BridgeError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
