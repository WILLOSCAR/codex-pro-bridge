#!/usr/bin/env python3
"""保存发送检查点并为当前宿主选择等待方式；不操作浏览器或创建定时任务。"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".shared"))
from bridge_store import BridgeError  # noqa: E402
from bridge_attempts import (  # noqa: E402
    fail_attempt, mark_send_started, prepare_attempt, read_attempt, record_submission,
    register_watcher, reserve_watcher, stop_watcher_record, wait_plan,
    browser_wait_script, browser_wait_exec,
)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--bridge-thread-id", required=True)
    sub = parser.add_subparsers(dest="command", required=True)
    prepare = sub.add_parser("prepare")
    prepare.add_argument("--prompt-file", required=True)
    prepare.add_argument("--preflight-file", required=True)
    prepare.add_argument(
        "--deadline",
        default="",
        help="Optional timezone-aware business deadline; omit for an unattended round with no business cutoff",
    )
    for name in ("status", "send-started", "submitted", "wait-plan", "wait-script", "reserve-watcher",
                 "register-watcher", "watcher-stopped", "fail"):
        command = sub.add_parser(name)
        command.add_argument("--attempt-id", required=True)
        if name in {"wait-plan", "reserve-watcher"}:
            command.add_argument("--capabilities-file", required=True)
        if name == "wait-script":
            command.add_argument("--observed-page-url", default="",
                                 help="Current URL returned by resolve-tab, including any changed Project slug")
            command.add_argument("--exec-page-id", default="",
                                 help="Emit a functions.exec program for this resolved Chrome DevTools page")
        if name in {"register-watcher", "watcher-stopped"}:
            command.add_argument("--automation-id", required=True)
        if name == "fail":
            command.add_argument("--reason", required=True)
        if name == "submitted":
            command.add_argument("--observation-file", required=True,
                                 help="Successful check_browser_recovery JSON for the exact submitted turn")
            command.add_argument("--submitted-at", default="")
    args = parser.parse_args()
    try:
        repo = Path(args.repo).resolve()
        if not repo.is_dir():
            raise BridgeError("Repository root must exist")
        thread_id = args.bridge_thread_id
        def read_json(path):
            value = json.loads(Path(path).read_text(encoding="utf-8"))
            if not isinstance(value, dict):
                raise BridgeError("Input must be a JSON object")
            return value
        if args.command == "prepare":
            result = prepare_attempt(repo, thread_id, Path(args.prompt_file).read_text(encoding="utf-8"),
                                     read_json(args.preflight_file), args.deadline or None)
        elif args.command == "status":
            result = read_attempt(repo, thread_id, args.attempt_id)
        elif args.command == "send-started":
            result = mark_send_started(repo, thread_id, args.attempt_id)
        elif args.command == "submitted":
            observation = read_json(args.observation_file)
            if (observation.get("ready") is not True or
                    observation.get("action") != "do-not-resend-read-existing-turn"):
                raise BridgeError("Submission needs verified user-turn recovery observations")
            result = record_submission(repo, thread_id, args.attempt_id,
                conversation_url=observation["observed_page_url"], owner=observation["tab_owner_token"],
                prompt_sha256=observation["expected_prompt_sha256"],
                boundary=observation["pre_submit_boundary"], remote_turn_id=observation["observed_user_turn_id"],
                after_boundary=observation["observed_user_turn_after_boundary"], submitted_at=args.submitted_at)
        elif args.command == "wait-plan":
            result = wait_plan(read_attempt(repo, thread_id, args.attempt_id), read_json(args.capabilities_file))
        elif args.command == "wait-script":
            attempt = read_attempt(repo, thread_id, args.attempt_id)
            if args.exec_page_id:
                print(browser_wait_exec(attempt, args.exec_page_id, args.observed_page_url))
                return 0
            result = browser_wait_script(attempt, args.observed_page_url)
        elif args.command == "reserve-watcher":
            result = reserve_watcher(repo, thread_id, args.attempt_id, read_json(args.capabilities_file))
        elif args.command == "register-watcher":
            result = register_watcher(repo, thread_id, args.attempt_id, args.automation_id)
        elif args.command == "watcher-stopped":
            result = stop_watcher_record(repo, thread_id, args.attempt_id, args.automation_id)
        else:
            result = fail_attempt(repo, thread_id, args.attempt_id, args.reason)
        # Prompt and preflight remain durable on disk without repeating the bundle context in tools.
        print(json.dumps({k: v for k, v in result.items() if k not in {"prompt", "preflight"}},
                         ensure_ascii=False, sort_keys=True))
        return 0
    except (BridgeError, OSError, ValueError, KeyError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
