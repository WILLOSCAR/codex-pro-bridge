#!/usr/bin/env python3
"""Build an immutable, side-effect-free ChatGPT Project Source sync plan."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import BridgeError  # noqa: E402
from source_manifest import ProjectSourceManager, SOURCE_ROLES  # noqa: E402


def read_json_file(value: str, repo: Path) -> object:
    if not value:
        return []
    path = Path(value).expanduser()
    path = path.resolve() if path.is_absolute() else (repo / path).resolve()
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Plan Project Source uploads, reuse, and safe managed replacement."
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--bridge-project-id", default="")
    parser.add_argument("--source", action="append", default=[])
    parser.add_argument(
        "--role",
        action="append",
        default=[],
        metavar="PATH=ROLE",
        help="Assign a source role: " + ", ".join(sorted(SOURCE_ROLES)),
    )
    parser.add_argument(
        "--remote-inventory-file",
        required=True,
        help="JSON inventory observed from the bound ChatGPT Project; use [] when empty.",
    )
    parser.add_argument("--max-project-files", type=int, default=0)
    parser.add_argument("--max-source-bytes", type=int, default=512 * 1024 * 1024)
    parser.add_argument("--allow-secret-like-content", action="store_true")
    args = parser.parse_args()
    try:
        roles: dict[str, str] = {}
        for value in args.role:
            if "=" not in value:
                raise BridgeError("--role must use PATH=ROLE")
            path, role = value.rsplit("=", 1)
            roles[path] = role
        repo = Path(args.repo).expanduser().resolve()
        manager = ProjectSourceManager(repo)
        project_id = manager.store.resolve_project_id(args.bridge_project_id)
        plan, plan_path = manager.plan(
            project_id,
            source_paths=args.source,
            source_roles=roles,
            remote_inventory=read_json_file(args.remote_inventory_file, repo),
            max_project_files=args.max_project_files,
            max_source_bytes=args.max_source_bytes,
            allow_secret_like_content=args.allow_secret_like_content,
        )
        print(
            json.dumps(
                {"plan_path": str(plan_path), "plan": plan},
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            )
        )
        return 0 if plan["ready"] else 2
    except (BridgeError, OSError, json.JSONDecodeError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
