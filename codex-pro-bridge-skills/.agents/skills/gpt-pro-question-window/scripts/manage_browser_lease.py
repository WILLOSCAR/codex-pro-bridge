#!/usr/bin/env python3
"""Manage host-local, conversation-scoped browser ownership.

Different ChatGPT conversations may use different dedicated tabs concurrently.
A live Project claim excludes conversation mutations in that Project while
shared Project state is changed. Conversation-to-Bridge-Thread bindings persist
across releases and MCP/VS Code reloads.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import (  # noqa: E402
    DEFAULT_BROWSER_LEASE_TTL_SECONDS,
    DEFAULT_BROWSER_PROFILE,
    BridgeError,
    acquire_browser_lease,
    assert_browser_lease_held,
    read_browser_lease,
    release_browser_lease,
)
from browser_host import cleanup_staged_file  # noqa: E402
from browser_identity import (  # noqa: E402
    prepare_bootstrap_tab,
    promote_bootstrap_tab,
    resolve_owned_tab,
)
from browser_observations import parse_json_observation  # noqa: E402


def release_argv(token: str, terminal_state: str = "", bootstrap_cleanup: str = "") -> list[str]:
    argv = ["release", "--token", token]
    if terminal_state:
        argv.extend(["--terminal-state", terminal_state])
    if bootstrap_cleanup:
        argv.extend(["--bootstrap-tab-cleanup", bootstrap_cleanup])
    return argv


def add_safe_release_argv(result: dict) -> dict:
    enriched = dict(result)
    if not result.get("bootstrap", False):
        enriched["release_argv"] = release_argv(result["token"])
    return enriched


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Manage host-local browser claims scoped by profile, Project, or conversation."
    )
    parser.add_argument("--repo", default=".")
    sub = parser.add_subparsers(dest="command", required=True)

    acquire = sub.add_parser("acquire", help="Acquire a scoped claim or fail on conflict.")
    acquire.add_argument("--holder", required=True, help="Unique worker or agent identifier.")
    acquire.add_argument("--bridge-thread-id", default="")
    acquire.add_argument("--expected-conversation-id", default="")
    acquire.add_argument("--expected-remote-project-id", default="")
    acquire.add_argument("--browser-profile", default=DEFAULT_BROWSER_PROFILE)
    acquire.add_argument(
        "--scope",
        choices=("auto", "profile", "project", "conversation"),
        default="auto",
        help="auto chooses conversation, then Project, then whole profile.",
    )
    acquire.add_argument(
        "--bootstrap",
        action="store_true",
        help="Own a Project/home new-chat tab until its first Send yields a conversation URL.",
    )
    acquire.add_argument(
        "--ttl-seconds", type=int, default=DEFAULT_BROWSER_LEASE_TTL_SECONDS
    )

    release = sub.add_parser("release", help="Release one exact live claim.")
    release.add_argument("--token", required=True)
    release.add_argument("--staged-path", default="")
    release.add_argument("--staged-sha256", default="")
    release.add_argument(
        "--terminal-state",
        choices=("send-accepted", "dry-run", "failed", "captured"),
        default="",
        help="Required with staged cleanup; cleanup happens before claim release.",
    )
    release.add_argument(
        "--bootstrap-tab-cleanup",
        choices=("owner-token-cleared", "owner-token-never-bound"),
        default="",
        help="Required before releasing an unpromoted bootstrap claim.",
    )

    inspect = sub.add_parser("assert", help="Verify and print one live claim.")
    inspect.add_argument("--token", required=True)

    promote = sub.add_parser(
        "promote-bootstrap",
        help="Atomically bind a post-Send conversation URL to a bootstrap claim.",
    )
    promote.add_argument("--token", required=True)
    promote.add_argument("--bridge-thread-id", required=True)
    promote.add_argument("--browser-profile", default=DEFAULT_BROWSER_PROFILE)
    promote.add_argument("--expected-remote-project-id", default="")
    promote.add_argument("--observed-page-url", required=True)
    promote.add_argument("--matching-page-count", type=int, required=True)
    promote.add_argument("--observed-page-id", required=True)
    promote.add_argument("--snapshot-page-id", required=True)
    promote.add_argument("--observed-tab-owner-token", required=True)

    prepare = sub.add_parser(
        "prepare-bootstrap-tab",
        help="Reuse, safely reclaim, or authorize opening one canonical new-chat tab.",
    )
    prepare.add_argument("--token", required=True)
    prepare.add_argument("--bridge-thread-id", required=True)
    prepare.add_argument("--browser-profile", default=DEFAULT_BROWSER_PROFILE)
    prepare.add_argument("--expected-remote-project-id", default="")
    prepare.add_argument("--matching-page-count", type=int, required=True)
    prepare.add_argument("--observed-page-url", default="")
    prepare.add_argument("--observed-page-id", default="")
    prepare.add_argument("--observed-tab-owner-token", default="")
    prepare.add_argument("--pages-json", default="")
    prepare.add_argument("--owners-json", default="")

    resolve = sub.add_parser(
        "resolve-tab",
        help="Resolve one owned tab from the complete MCP page and owner observations.",
    )
    resolve.add_argument("--token", required=True)
    resolve.add_argument("--bridge-thread-id", required=True)
    resolve.add_argument("--browser-profile", default=DEFAULT_BROWSER_PROFILE)
    resolve.add_argument("--expected-remote-project-id", default="")
    resolve.add_argument("--pages-json", required=True)
    resolve.add_argument("--owners-json", default="")

    sub.add_parser("status", help="Print all live claims and durable conversation bindings.")

    args = parser.parse_args()
    try:
        repo = Path(args.repo).resolve()
        if not repo.is_dir():
            raise BridgeError(f"Repository root is not a directory: {repo}")
        if args.command == "acquire":
            result = add_safe_release_argv(acquire_browser_lease(
                repo,
                holder=args.holder,
                thread_id=args.bridge_thread_id,
                expected_conversation_id=args.expected_conversation_id,
                expected_remote_project_id=args.expected_remote_project_id,
                browser_profile=args.browser_profile,
                scope=args.scope,
                bootstrap=args.bootstrap,
                ttl_seconds=args.ttl_seconds,
            ))
        elif args.command == "release":
            staged_path = args.staged_path.strip()
            staged_sha256 = args.staged_sha256.strip()
            terminal_state = args.terminal_state.strip()
            if bool(staged_path) != bool(staged_sha256):
                raise BridgeError("--staged-path and --staged-sha256 must be supplied together")
            if staged_path and not terminal_state:
                raise BridgeError("--terminal-state is required when cleaning a staged file")
            if terminal_state == "captured" and staged_path:
                raise BridgeError("captured release does not perform staged-file cleanup")
            claim = assert_browser_lease_held(repo, token=args.token)
            if terminal_state == "send-accepted" and claim.get("bootstrap", False):
                raise BridgeError(
                    "A successful first Send must promote its bootstrap claim before release"
                )
            if terminal_state == "captured" and (
                claim.get("bootstrap", False) or claim.get("scope") != "conversation"
            ):
                raise BridgeError("captured release requires a promoted conversation claim")
            if claim.get("bootstrap", False) and not args.bootstrap_tab_cleanup:
                raise BridgeError(
                    "An unpromoted bootstrap release requires --bootstrap-tab-cleanup "
                    "after the owned sessionStorage token is cleared or was never bound"
                )
            cleanup = (
                cleanup_staged_file(staged_path, expected_sha256=staged_sha256)
                if staged_path
                else {"cleaned": False}
            )
            result = {
                "released": release_browser_lease(repo, token=args.token),
                "terminal_state": terminal_state,
                "staging_cleanup": cleanup,
                "scope": claim.get("scope", ""),
                "thread_id": claim.get("thread_id", ""),
                "expected_conversation_id": claim.get("expected_conversation_id", ""),
                "bootstrap_tab_cleanup": args.bootstrap_tab_cleanup,
            }
        elif args.command == "assert":
            result = assert_browser_lease_held(repo, token=args.token)
        elif args.command == "promote-bootstrap":
            result = promote_bootstrap_tab(
                repo,
                claim_token=args.token,
                thread_id=args.bridge_thread_id,
                browser_profile=args.browser_profile,
                expected_project_id=args.expected_remote_project_id,
                observed_page_url=args.observed_page_url,
                matching_page_count=args.matching_page_count,
                observed_page_id=args.observed_page_id,
                snapshot_page_id=args.snapshot_page_id,
                observed_tab_owner_token=args.observed_tab_owner_token,
            )
            result["release_argv"] = release_argv(args.token, "send-accepted")
        elif args.command == "prepare-bootstrap-tab":
            pages = parse_json_observation(args.pages_json, "--pages-json") if args.pages_json else None
            owners = (
                parse_json_observation(args.owners_json, "--owners-json", owners=True)
                if args.owners_json
                else None
            )
            result = prepare_bootstrap_tab(
                repo,
                claim_token=args.token,
                thread_id=args.bridge_thread_id,
                browser_profile=args.browser_profile,
                expected_project_id=args.expected_remote_project_id,
                matching_page_count=args.matching_page_count,
                observed_page_url=args.observed_page_url,
                observed_page_id=args.observed_page_id,
                observed_tab_owner_token=args.observed_tab_owner_token,
                pages=pages,
                owners=owners,
            )
        elif args.command == "resolve-tab":
            result = resolve_owned_tab(
                repo,
                claim_token=args.token,
                thread_id=args.bridge_thread_id,
                browser_profile=args.browser_profile,
                expected_project_id=args.expected_remote_project_id,
                pages=parse_json_observation(args.pages_json, "--pages-json"),
                owners=(
                    parse_json_observation(args.owners_json, "--owners-json", owners=True)
                    if args.owners_json
                    else None
                ),
            )
            claim = assert_browser_lease_held(repo, token=args.token)
            if not claim.get("bootstrap", False):
                result["release_argv"] = release_argv(args.token)
        else:
            result = read_browser_lease(repo)
        print(json.dumps(result, ensure_ascii=False, sort_keys=True))
        return 0
    except (BridgeError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
