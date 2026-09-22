#!/usr/bin/env python3
"""Choose one fail-closed action after MCP or VS Code browser reconnection."""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_attempts import record_submission, recovery_guard
from bridge_store import DEFAULT_BROWSER_PROFILE, BridgeError, now_iso
from browser_identity import (
    resolve_owned_tab,
    verify_bootstrap_tab,
    verify_claimed_tab,
)
from browser_observations import parse_json_observation

SHA256_RE = re.compile(r"^[0-9a-f]{64}$")


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Decide whether a reconnected Bridge may upload, reuse, or must not resend."
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--bridge-thread-id", required=True)
    parser.add_argument("--browser-lease-token", required=True)
    parser.add_argument("--browser-profile", default=DEFAULT_BROWSER_PROFILE)
    parser.add_argument("--expected-project-id", default="")
    parser.add_argument("--expected-conversation-id", default="")
    parser.add_argument("--conversation-bootstrap", action="store_true")
    parser.add_argument("--pages-json", default="")
    parser.add_argument("--owners-json", default="")
    parser.add_argument("--observed-page-url", required=True)
    parser.add_argument("--matching-page-count", type=int, required=True)
    parser.add_argument("--observed-page-id", required=True)
    parser.add_argument("--snapshot-page-id", required=True)
    parser.add_argument("--observed-tab-owner-token", required=True)
    parser.add_argument("--pre-submit-boundary", required=True)
    parser.add_argument("--expected-prompt-sha256", required=True)
    parser.add_argument("--attempt-id", default="", help="Durable attempt; otherwise detect the unfinished attempt")
    parser.add_argument("--expected-attachment-name", default="")
    parser.add_argument("--observed-attachment-name", action="append", default=[])
    parser.add_argument(
        "--composer-state",
        choices=(
            "empty",
            "expected-attachment",
            "unexpected-attachment",
            "submitted",
            "generating",
            "completed",
            "ambiguous",
        ),
        required=True,
    )
    parser.add_argument("--observed-user-turn-sha256", default="")
    parser.add_argument("--observed-user-turn-id", default="")
    parser.add_argument(
        "--observed-user-turn-after-boundary",
        choices=("yes", "no", "ambiguous"),
        default="",
    )
    args = parser.parse_args()

    try:
        expected_prompt = args.expected_prompt_sha256.strip().lower()
        if not SHA256_RE.fullmatch(expected_prompt):
            raise BridgeError("--expected-prompt-sha256 must be 64 lowercase hex characters")
        if not args.pre_submit_boundary.strip():
            raise BridgeError("A pre-submit turn boundary is required")
        expected_conversation_id = args.expected_conversation_id.strip()
        if args.conversation_bootstrap:
            if expected_conversation_id:
                raise BridgeError("Bootstrap recovery cannot include a conversation id")
            if args.pre_submit_boundary.strip() != "new-conversation":
                raise BridgeError("Bootstrap recovery requires boundary new-conversation")
            if not args.pages_json:
                raise BridgeError("Bootstrap recovery requires the complete --pages-json")
        elif not expected_conversation_id:
            raise BridgeError("Conversation recovery requires --expected-conversation-id")

        repo = Path(args.repo).resolve()
        if not repo.is_dir():
            raise BridgeError(f"Repository root is not a directory: {repo}")
        # Promotion may be reconciled before the composer is observable, but it never authorizes Send.
        attempt = recovery_guard(repo, args.bridge_thread_id, args.attempt_id,
                                 prompt_sha256=expected_prompt, boundary=args.pre_submit_boundary,
                                 owner=args.observed_tab_owner_token, composer_state="ambiguous")
        resolved = None
        if args.pages_json:
            pages = parse_json_observation(args.pages_json, "--pages-json")
            owners = (
                parse_json_observation(args.owners_json, "--owners-json", owners=True)
                if args.owners_json
                else None
            )
            resolved = resolve_owned_tab(
                repo,
                claim_token=args.browser_lease_token,
                thread_id=args.bridge_thread_id,
                browser_profile=args.browser_profile,
                expected_project_id=args.expected_project_id,
                pages=pages,
                owners=owners,
            )
            if resolved["action"] == "promote-ready" and args.conversation_bootstrap:
                if args.matching_page_count != resolved["matching_page_count"]:
                    raise BridgeError(
                        "Recovery matching-page-count disagrees with the complete page list"
                    )
                if str(resolved["page_id"]) != str(args.snapshot_page_id).strip():
                    raise BridgeError("Promotion-ready page does not match the fresh snapshot pageId")
                print(
                    json.dumps(
                        {
                            "ready": True,
                            "checked_at": now_iso(),
                            "action": "promote-bootstrap-do-not-resend",
                            "page_id": resolved["page_id"],
                            "url": resolved["url"],
                            "conversation_id": resolved["conversation_id"],
                            "tab_owner_token": resolved["tab_owner_token"],
                            "matching_page_count": resolved["matching_page_count"],
                            "owner_selected_count": resolved.get("owner_selected_count", 1),
                            "pre_submit_boundary": args.pre_submit_boundary.strip(),
                            "expected_prompt_sha256": expected_prompt,
                            "page_transition_evidence": "current-same-page-observation-only",
                            "navigation_authorized": False,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    )
                )
                return 0
            if resolved["action"] != "reuse-owned-tab":
                raise BridgeError(
                    f"Recovery tab resolution returned {resolved['action']!r} "
                    f"({resolved.get('reason', 'no-reason')}); observations conflict. "
                    "This does not prove that the owned page navigated to Project home or any "
                    "candidate URL. HOLD without navigation, reload, or opening a page; reacquire "
                    "and collect a fresh location.href plus owner from the same pageId."
                )
            if args.matching_page_count != resolved["matching_page_count"]:
                raise BridgeError(
                    "Recovery matching-page-count disagrees with the complete page list"
                )
            if (
                str(resolved["page_id"]) != str(args.observed_page_id).strip()
                or resolved["url"] != args.observed_page_url.strip()
            ):
                raise BridgeError(
                    "Recovery observations do not match the resolved owned tab. This is an "
                    "observation mismatch, not evidence of page navigation; HOLD without "
                    "navigating and recollect the same pageId's live URL and owner."
                )

        if args.conversation_bootstrap:
            tab = verify_bootstrap_tab(
                repo,
                claim_token=args.browser_lease_token,
                thread_id=args.bridge_thread_id,
                browser_profile=args.browser_profile,
                expected_project_id=args.expected_project_id,
                observed_page_url=args.observed_page_url,
                matching_page_count=args.matching_page_count,
                observed_page_id=args.observed_page_id,
                snapshot_page_id=args.snapshot_page_id,
                observed_tab_owner_token=args.observed_tab_owner_token,
                owner_selected_count=resolved.get("owner_selected_count", 0) if resolved else None,
            )
        else:
            tab = verify_claimed_tab(
                repo,
                claim_token=args.browser_lease_token,
                thread_id=args.bridge_thread_id,
                browser_profile=args.browser_profile,
                expected_project_id=args.expected_project_id,
                expected_conversation_id=expected_conversation_id,
                observed_page_url=args.observed_page_url,
                matching_page_count=args.matching_page_count,
                observed_page_id=args.observed_page_id,
                snapshot_page_id=args.snapshot_page_id,
                observed_tab_owner_token=args.observed_tab_owner_token,
                owner_selected_count=resolved.get("owner_selected_count", 0) if resolved else None,
            )

        state = args.composer_state
        if attempt:
            recovery_guard(repo, args.bridge_thread_id, attempt["attempt_id"],
                           prompt_sha256=expected_prompt, boundary=args.pre_submit_boundary,
                           owner=args.observed_tab_owner_token, composer_state=state)
        observed_attachments = [name.strip() for name in args.observed_attachment_name if name.strip()]
        expected_attachment = args.expected_attachment_name.strip()
        observed_turn_digest = args.observed_user_turn_sha256.strip().lower()
        observed_turn_id = args.observed_user_turn_id.strip()
        boundary_relation = args.observed_user_turn_after_boundary.strip()
        if observed_turn_digest and not SHA256_RE.fullmatch(observed_turn_digest):
            raise BridgeError("--observed-user-turn-sha256 must be 64 lowercase hex characters")

        if state in {"unexpected-attachment", "ambiguous"}:
            raise BridgeError(f"Recovery state {state!r} is ambiguous; HOLD without browser mutation")
        if state == "empty":
            if observed_attachments or observed_turn_digest or observed_turn_id or boundary_relation:
                raise BridgeError("An empty composer cannot include submitted-turn observations")
            action = "upload-and-run-preflight" if expected_attachment else "enter-prompt-and-run-preflight"
        elif state == "expected-attachment":
            if not expected_attachment or observed_attachments != [expected_attachment]:
                raise BridgeError("Visible composer attachment does not exactly match the expected file")
            if observed_turn_digest or observed_turn_id or boundary_relation:
                raise BridgeError("An unsubmitted composer cannot include submitted-turn observations")
            action = "reuse-attachment-and-run-preflight"
        else:
            if observed_attachments:
                raise BridgeError("A submitted turn cannot be recovered from a composer with attachments")
            if not observed_turn_id:
                raise BridgeError("Submitted recovery requires the exact observed user turn id")
            if boundary_relation != "yes":
                raise BridgeError("The observed user turn is not proven to be after the pre-submit boundary")
            if observed_turn_digest != expected_prompt:
                raise BridgeError("Observed submitted user turn does not match the expected prompt digest")
            action = "do-not-resend-read-existing-turn"
            if attempt:
                record_submission(repo, args.bridge_thread_id, attempt["attempt_id"],
                                  conversation_url=args.observed_page_url,
                                  owner=args.observed_tab_owner_token,
                                  prompt_sha256=observed_turn_digest, boundary=args.pre_submit_boundary,
                                  remote_turn_id=observed_turn_id, after_boundary=boundary_relation)

        print(
            json.dumps(
                {
                    "ready": True,
                    "checked_at": now_iso(),
                    "action": action,
                    "composer_state": state,
                    "attempt_id": attempt["attempt_id"] if attempt else "",
                    "expected_prompt_sha256": expected_prompt,
                    "expected_attachment_name": expected_attachment,
                    "pre_submit_boundary": args.pre_submit_boundary.strip(),
                    "observed_user_turn_id": observed_turn_id,
                    "observed_user_turn_after_boundary": boundary_relation,
                    "conversation_id": tab.get("url_conversation_id", ""),
                    "matching_page_count": tab["matching_page_count"],
                    "owner_selected_count": resolved.get("owner_selected_count", 1) if resolved else 1,
                    "page_id": tab["observed_page_id"],
                    "observed_page_url": args.observed_page_url,
                    "page_transition_evidence": "current-same-page-observation-only",
                    "navigation_authorized": False,
                    "tab_owner_token": tab["tab_owner_token"],
                },
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except (BridgeError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
