#!/usr/bin/env python3
"""Create, bind, inspect, and manage one Bridge Project."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import BridgeError  # noqa: E402
from project_store import BridgeProjectStore, SYNC_MODES, TASK_STATUSES  # noqa: E402


def print_json(value: object) -> None:
    print(json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True))


def main() -> int:
    parser = argparse.ArgumentParser(description="Manage Codex Pro Bridge Project state.")
    parser.add_argument("--repo", default=".", help="Local project root.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    create = subparsers.add_parser("create", help="Create the local Bridge Project identity.")
    create.add_argument("--project-id", required=True)
    create.add_argument("--title", default="")
    create.add_argument("--brief", default="", help="Existing local project brief.")

    update = subparsers.add_parser(
        "update", help="Update the local Project title or project brief."
    )
    update.add_argument("--bridge-project-id", default="")
    update.add_argument("--title", default=None)
    update.add_argument("--brief", default=None)

    bind = subparsers.add_parser("bind", help="Bind an existing or newly created ChatGPT Project.")
    bind.add_argument("--bridge-project-id", default="")
    bind.add_argument("--remote-url", required=True)
    bind.add_argument("--remote-project-id", default="")
    bind.add_argument("--observed-title", default="")
    bind.add_argument("--workspace", default="")
    bind.add_argument("--account-label", default="")
    bind.add_argument("--sync-mode", choices=sorted(SYNC_MODES), default="append_only")
    bind.add_argument("--max-project-files", type=int, default=0)
    bind.add_argument("--rebind", action="store_true")

    verify_binding = subparsers.add_parser(
        "verify-binding", help="Record the currently observed ChatGPT Project identity."
    )
    verify_binding.add_argument("--bridge-project-id", default="")
    verify_binding.add_argument("--observed-url", required=True)
    verify_binding.add_argument("--observed-project-id", default="")
    verify_binding.add_argument("--observed-title", default="")
    verify_binding.add_argument("--workspace", default="")
    verify_binding.add_argument("--account-label", default="")

    missing_binding = subparsers.add_parser(
        "mark-binding-missing",
        help="Record that the bound ChatGPT Project is no longer accessible.",
    )
    missing_binding.add_argument("--bridge-project-id", default="")
    missing_binding.add_argument("--reason", default="")

    unbind = subparsers.add_parser(
        "unbind", help="Remove the active relationship without deleting remote content."
    )
    unbind.add_argument("--bridge-project-id", default="")
    unbind.add_argument("--reason", default="")

    archive = subparsers.add_parser(
        "archive", help="Archive local Project routing without deleting remote content."
    )
    archive.add_argument("--bridge-project-id", default="")
    archive.add_argument("--reason", default="")

    reactivate = subparsers.add_parser(
        "reactivate", help="Reactivate an archived local Bridge Project."
    )
    reactivate.add_argument("--bridge-project-id", default="")

    attach = subparsers.add_parser(
        "attach-task", help="Attach a Bridge Thread as a Project task."
    )
    attach.add_argument("--bridge-project-id", default="")
    attach.add_argument("--bridge-thread-id", required=True)
    attach.add_argument("--title", default="")
    attach.add_argument("--goal", default="")
    attach.add_argument("--status", choices=sorted(TASK_STATUSES), default="active")
    attach.add_argument("--depends-on", action="append", default=[])

    update_task = subparsers.add_parser(
        "update-task", help="Update Project task title, goal, or dependencies."
    )
    update_task.add_argument("--bridge-project-id", default="")
    update_task.add_argument("--bridge-thread-id", required=True)
    update_task.add_argument("--title", default=None)
    update_task.add_argument("--goal", default=None)
    update_task.add_argument("--depends-on", action="append", default=None)

    status = subparsers.add_parser("set-task-status", help="Change Project task status.")
    status.add_argument("--bridge-project-id", default="")
    status.add_argument("--bridge-thread-id", required=True)
    status.add_argument("--status", choices=sorted(TASK_STATUSES), required=True)

    show = subparsers.add_parser("show", help="Show current Project, binding, and task state.")
    show.add_argument("--bridge-project-id", default="")

    args = parser.parse_args()
    try:
        store = BridgeProjectStore(Path(args.repo))
        if args.command == "create":
            result = store.create_project(
                args.project_id, title=args.title, brief_path=args.brief
            )
        elif args.command == "update":
            project_id = store.resolve_project_id(args.bridge_project_id)
            result = store.update_project(
                project_id,
                title=args.title,
                brief_path=args.brief,
            )
        elif args.command == "bind":
            project_id = store.resolve_project_id(args.bridge_project_id)
            result = store.bind_remote(
                project_id,
                remote_url=args.remote_url,
                remote_project_id=args.remote_project_id,
                observed_title=args.observed_title,
                workspace=args.workspace,
                account_label=args.account_label,
                sync_mode=args.sync_mode,
                max_project_files=args.max_project_files,
                verified=False,
                allow_rebind=args.rebind,
            )
        elif args.command == "verify-binding":
            project_id = store.resolve_project_id(args.bridge_project_id)
            result = store.verify_remote_binding(
                project_id,
                observed_url=args.observed_url,
                observed_project_id=args.observed_project_id,
                observed_title=args.observed_title,
                workspace=args.workspace,
                account_label=args.account_label,
            )
        elif args.command == "mark-binding-missing":
            project_id = store.resolve_project_id(args.bridge_project_id)
            result = store.mark_remote_missing(project_id, reason=args.reason)
        elif args.command == "unbind":
            project_id = store.resolve_project_id(args.bridge_project_id)
            result = store.unbind_remote(project_id, reason=args.reason)
        elif args.command == "archive":
            project_id = store.resolve_project_id(args.bridge_project_id)
            result = store.archive_project(project_id, reason=args.reason)
        elif args.command == "reactivate":
            project_id = store.resolve_project_id(args.bridge_project_id)
            result = store.reactivate_project(project_id)
        elif args.command == "attach-task":
            project_id = store.resolve_project_id(args.bridge_project_id)
            result = store.attach_thread(
                project_id,
                args.bridge_thread_id,
                title=args.title,
                goal=args.goal,
                status=args.status,
                depends_on=args.depends_on,
            )
        elif args.command == "update-task":
            project_id = store.resolve_project_id(args.bridge_project_id)
            result = store.update_task(
                project_id,
                args.bridge_thread_id,
                title=args.title,
                goal=args.goal,
                depends_on=args.depends_on,
            )
        elif args.command == "set-task-status":
            project_id = store.resolve_project_id(args.bridge_project_id)
            result = store.set_task_status(
                project_id, args.bridge_thread_id, args.status
            )
        elif args.command == "show":
            project_id = store.resolve_project_id(args.bridge_project_id)
            result = {
                "project": store.load_project(project_id),
                "binding": store.load_binding(project_id),
                "tasks": list(store.task_states(project_id).values()),
                "verification": store.verify(project_id),
            }
        else:
            raise BridgeError(f"Unsupported command: {args.command}")
        print_json(result)
        return 0
    except (BridgeError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
