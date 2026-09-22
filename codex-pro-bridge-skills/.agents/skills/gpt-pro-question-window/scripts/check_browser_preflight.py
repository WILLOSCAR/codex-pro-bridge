#!/usr/bin/env python3
"""Fail-closed browser, identity, and WSL/Windows staging preflight."""

from __future__ import annotations

import argparse
import json
import ntpath
import re
import sys
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_attempts import assert_preflight_allowed
from bridge_store import DEFAULT_BROWSER_PROFILE, BridgeError, now_iso
from browser_host import staging_windows_root, verify_staged_file
from browser_identity import (
    match_listed_pages,
    normalize_page_id,
    resolve_owned_tab,
    verify_bootstrap_tab,
    verify_claimed_tab,
)
from browser_observations import parse_json_observation
from devtools_upload import validate_upload_action_plan, validate_upload_receipt
from model_controls import (
    MODEL_SELECTION_KINDS,
    assess_model_selection,
    validate_model_control_trace,
)
from project_store import REMOTE_PROJECT_ID_RE


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify exact browser identity, model, attachment, and host staging before Send."
    )
    parser.add_argument("--repo", default=".")
    parser.add_argument("--bridge-thread-id", required=True)
    parser.add_argument("--browser-lease-token", required=True)
    parser.add_argument("--browser-profile", default=DEFAULT_BROWSER_PROFILE)
    parser.add_argument("--requested-model", required=True)
    parser.add_argument("--selected-ui-label", required=True)
    parser.add_argument(
        "--model-selection-kind",
        choices=MODEL_SELECTION_KINDS,
        default="exact",
        help="Use latest-alias when the checked UI item is a dynamic label such as 最新.",
    )
    parser.add_argument("--requested-thinking-intensity", required=True)
    parser.add_argument("--selected-thinking-intensity", required=True)
    parser.add_argument(
        "--model-control-receipt",
        default="",
        help="Repo-local model-controls/v1 receipt for the bounded pre-Send control transaction.",
    )
    parser.add_argument("--source-bundle", default="")
    parser.add_argument("--bundle", default="", help="Absolute WSL path of the G-drive staged file.")
    parser.add_argument("--attachment-name", default="")
    parser.add_argument("--upload-control", default="")
    parser.add_argument(
        "--upload-action-plan",
        default="",
        help="Repo-local devtools-upload/v1 plan checked against staging and page observations.",
    )
    parser.add_argument(
        "--upload-result",
        "--upload-receipt",
        dest="upload_result",
        default="",
        help="Successful repo-local upload receipt produced by validate_devtools_upload.py.",
    )
    parser.add_argument("--mcp-host-os", choices=("windows",), required=True)
    parser.add_argument("--browser-host-os", choices=("windows",), required=True)
    parser.add_argument("--mcp-temp-root", required=True)
    parser.add_argument("--expected-project-id", default="")
    parser.add_argument("--observed-project-id", default="")
    parser.add_argument("--expected-workspace", default="")
    parser.add_argument("--observed-workspace", default="")
    parser.add_argument("--expected-account-label", default="")
    parser.add_argument("--observed-account-label", default="")
    parser.add_argument("--binding-status", default="")
    parser.add_argument("--expected-conversation-id", default="")
    parser.add_argument("--observed-conversation-id", default="")
    parser.add_argument(
        "--conversation-bootstrap",
        action="store_true",
        help="Preflight a Project/home new-chat tab before its first Send creates an id.",
    )
    parser.add_argument("--observed-page-url", required=True)
    parser.add_argument("--matching-page-count", type=int, required=True)
    parser.add_argument(
        "--pages-json",
        required=True,
        help="Complete raw MCP list_pages result or normalized page array.",
    )
    parser.add_argument(
        "--owners-json",
        default="",
        help="Owner observations for every listed ChatGPT page, with each raw result scoped by pageId.",
    )
    parser.add_argument("--observed-page-id", required=True)
    parser.add_argument("--snapshot-page-id", required=True)
    parser.add_argument("--observed-tab-owner-token", required=True)
    parser.add_argument("--pre-submit-boundary", required=True)
    parser.add_argument("--prompt-sha256", required=True)
    args = parser.parse_args()

    try:
        requested = args.requested_model.strip()
        selected = args.selected_ui_label.strip()
        model_verification = assess_model_selection(
            requested, selected, args.model_selection_kind
        )
        if model_verification == "unverified":
            raise BridgeError("Requested and selected model labels must both be non-empty")
        if model_verification == "mismatch":
            raise BridgeError(
                f"Selected UI label {selected!r} does not exactly match requested model {requested!r}"
            )
        requested_intensity = args.requested_thinking_intensity.strip()
        selected_intensity = args.selected_thinking_intensity.strip()
        if not requested_intensity or not selected_intensity:
            raise BridgeError("Requested and selected thinking intensities must both be non-empty")
        if requested_intensity != selected_intensity:
            raise BridgeError(
                f"Selected thinking intensity {selected_intensity!r} does not exactly match "
                f"requested intensity {requested_intensity!r}"
            )
        pre_submit_boundary = args.pre_submit_boundary.strip()
        prompt_sha256 = args.prompt_sha256.strip().lower()
        if not pre_submit_boundary:
            raise BridgeError("A pre-submit turn boundary is required")
        if args.conversation_bootstrap and pre_submit_boundary != "new-conversation":
            raise BridgeError(
                "A conversation bootstrap must use --pre-submit-boundary new-conversation"
            )
        if not re.fullmatch(r"[0-9a-f]{64}", prompt_sha256):
            raise BridgeError("--prompt-sha256 must be 64 lowercase hex characters")

        expected_temp = ntpath.normcase(ntpath.normpath(str(staging_windows_root())))
        observed_temp = ntpath.normcase(ntpath.normpath(args.mcp_temp_root.strip()))
        if observed_temp != expected_temp:
            raise BridgeError(
                f"Observed MCP temp root {args.mcp_temp_root!r} does not match {str(staging_windows_root())!r}"
            )

        repo = Path(args.repo).resolve()
        if not repo.is_dir():
            raise BridgeError(f"Repository root is not a directory: {repo}")
        raw_bundle = Path(args.bundle).expanduser() if args.bundle else None
        raw_source = Path(args.source_bundle).expanduser() if args.source_bundle else None
        attachment_name = args.attachment_name.strip()
        upload_control = args.upload_control.strip()
        staged_verification = None
        attachment_sha256 = ""
        attachment_verification = "not-required"
        upload_action_plan = None
        upload_receipt = None
        model_control_receipt = None
        devtools_upload = "devtools" in upload_control.casefold()
        if raw_bundle:
            if not raw_bundle.is_absolute():
                raise BridgeError("--bundle must be an absolute WSL staging path")
            if not raw_source or not raw_source.is_absolute():
                raise BridgeError("--source-bundle must be an absolute path when a bundle is attached")
            staged_verification = verify_staged_file(raw_bundle, source_path=raw_source)
            if attachment_name != staged_verification["attachment_name"]:
                raise BridgeError(
                    f"Visible attachment {attachment_name!r} does not match staged bundle "
                    f"{staged_verification['attachment_name']!r}"
                )
            if not upload_control:
                raise BridgeError("--upload-control is required when a bundle is attached")
            if "hidden" in upload_control.lower():
                raise BridgeError("Direct hidden-input clicks are not an accepted upload control")
            attachment_sha256 = staged_verification["staged_sha256"]
            attachment_verification = "verified"
        elif raw_source or attachment_name or upload_control:
            raise BridgeError("Attachment observations require both --source-bundle and --bundle")
        if devtools_upload and not raw_bundle:
            raise BridgeError("DevTools upload-control requires an attached bundle")
        if devtools_upload and not args.upload_action_plan:
            raise BridgeError("DevTools upload-control requires --upload-action-plan")
        if devtools_upload and not args.upload_result:
            raise BridgeError("DevTools upload-control requires --upload-result success receipt")
        if args.upload_result and not args.upload_action_plan:
            raise BridgeError("--upload-result requires --upload-action-plan")
        if args.upload_action_plan:
            plan_path = Path(args.upload_action_plan).expanduser()
            if (
                not plan_path.is_absolute()
                or plan_path.is_symlink()
                or not plan_path.resolve().is_relative_to(repo)
            ):
                raise BridgeError("--upload-action-plan must be an absolute path inside the repository")
            try:
                plan_data = json.loads(plan_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise BridgeError(f"Cannot read upload action plan: {plan_path}") from exc
            upload_action_plan = validate_upload_action_plan(plan_data)
            if staged_verification is None:
                raise BridgeError("--upload-action-plan requires an attached staged bundle")
            if upload_action_plan["page_id"] != normalize_page_id(args.observed_page_id):
                raise BridgeError("Upload action plan page_id does not match preflight page")
            if upload_action_plan["windows_path"] != staged_verification["staged_windows_path"]:
                raise BridgeError("Upload action plan path does not match staged Windows path")
            if upload_action_plan["staged_sha256"] != staged_verification["staged_sha256"]:
                raise BridgeError("Upload action plan digest does not match staged bundle")
            if upload_action_plan["expected_attachment_name"] != attachment_name:
                raise BridgeError("Upload action plan attachment name does not match preflight")
            if "devtools" not in upload_control.casefold():
                raise BridgeError(
                    "A DevTools upload action plan requires a DevTools upload-control observation"
                )
            result_path = Path(args.upload_result).expanduser() if args.upload_result else None
            if result_path is None:
                raise BridgeError("DevTools upload-control requires --upload-result success receipt")
            if (
                not result_path.is_absolute()
                or result_path.is_symlink()
                or not result_path.resolve().is_relative_to(repo)
            ):
                raise BridgeError("--upload-result must be an absolute path inside the repository")
            if result_path.resolve().parent != plan_path.resolve().parent:
                raise BridgeError("--upload-result must share the action plan's artifact directory")
            try:
                result_data = json.loads(result_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise BridgeError(f"Cannot read upload result receipt: {result_path}") from exc
            upload_receipt = validate_upload_receipt(plan_data, result_data)
            if upload_receipt["page_id"] != normalize_page_id(args.observed_page_id):
                raise BridgeError("Upload receipt page_id does not match preflight page")
            if upload_receipt["windows_path"] != staged_verification["staged_windows_path"]:
                raise BridgeError("Upload receipt path does not match staged Windows path")
            if upload_receipt["staged_sha256"] != staged_verification["staged_sha256"]:
                raise BridgeError("Upload receipt digest does not match staged bundle")
            if upload_receipt["attachment_name"] != attachment_name:
                raise BridgeError("Upload receipt attachment name does not match preflight")
        elif args.upload_result:
            raise BridgeError("--upload-result requires --upload-action-plan")

        if devtools_upload and not args.model_control_receipt:
            raise BridgeError("DevTools upload-control requires --model-control-receipt")
        if args.model_control_receipt:
            control_path = Path(args.model_control_receipt).expanduser()
            if (
                not control_path.is_absolute()
                or control_path.is_symlink()
                or not control_path.resolve().is_relative_to(repo)
            ):
                raise BridgeError(
                    "--model-control-receipt must be an absolute non-symlink path inside the repository"
                )
            if args.upload_action_plan:
                plan_path = Path(args.upload_action_plan).expanduser().resolve()
                if control_path.resolve().parent != plan_path.parent:
                    raise BridgeError(
                        "--model-control-receipt must share the upload plan's artifact directory"
                    )
            try:
                control_data = json.loads(control_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                raise BridgeError(f"Cannot read model-control receipt: {control_path}") from exc
            model_control_receipt = validate_model_control_trace(control_data)
            if model_control_receipt["page_id"] != normalize_page_id(args.observed_page_id):
                raise BridgeError("Model-control receipt page_id does not match preflight page")
            if model_control_receipt["tab_owner_token"] != args.observed_tab_owner_token.strip():
                raise BridgeError("Model-control receipt owner does not match preflight owner")
            if model_control_receipt["observed_page_url"] != args.observed_page_url.strip():
                raise BridgeError("Model-control receipt URL does not match preflight URL")
            expected_controls = {
                "requested_model": requested,
                "selected_model": selected,
                "model_selection_kind": args.model_selection_kind,
                "requested_thinking_intensity": requested_intensity,
                "selected_thinking_intensity": selected_intensity,
            }
            for field, expected in expected_controls.items():
                if model_control_receipt[field] != expected:
                    raise BridgeError(
                        f"Model-control receipt {field} does not match preflight observation"
                    )

        expected_project_id = args.expected_project_id.strip()
        observed_project_id = args.observed_project_id.strip()
        expected_workspace = args.expected_workspace.strip()
        observed_workspace = args.observed_workspace.strip()
        expected_account_label = args.expected_account_label.strip()
        observed_account_label = args.observed_account_label.strip()
        binding_status = args.binding_status.strip()
        project_verification = "not-required"
        if expected_project_id:
            if not REMOTE_PROJECT_ID_RE.fullmatch(expected_project_id):
                raise BridgeError("--expected-project-id must look like g-p-<id>")
            if binding_status != "active":
                raise BridgeError("Project-bound submission requires an active binding")
            if observed_project_id != expected_project_id:
                raise BridgeError(
                    f"Observed ChatGPT Project {observed_project_id!r} does not match "
                    f"the binding {expected_project_id!r}"
                )
            if not expected_workspace or not expected_account_label:
                raise BridgeError(
                    "Project-bound submission requires expected workspace and account labels"
                )
            if observed_workspace != expected_workspace:
                raise BridgeError(
                    f"Observed workspace {observed_workspace!r} does not match "
                    f"the binding {expected_workspace!r}"
                )
            if observed_account_label != expected_account_label:
                raise BridgeError(
                    f"Observed account {observed_account_label!r} does not match "
                    f"the binding {expected_account_label!r}"
                )
            project_verification = "verified"
        elif (
            observed_project_id
            or expected_workspace
            or observed_workspace
            or expected_account_label
            or observed_account_label
            or binding_status
        ):
            raise BridgeError("Standalone submission cannot include Project binding observations")

        expected_conversation_id = args.expected_conversation_id.strip()
        observed_conversation_id = args.observed_conversation_id.strip()
        pages = parse_json_observation(args.pages_json, "--pages-json")
        page_matches = match_listed_pages(
            pages,
            expected_project_id=expected_project_id,
            expected_conversation_id=expected_conversation_id,
            bootstrap=args.conversation_bootstrap,
        )
        selected_matches = (
            page_matches["canonical_matches"]
            if args.conversation_bootstrap
            else page_matches["conversation_matches"]
        )
        if len(selected_matches) != args.matching_page_count:
            raise BridgeError(
                "--matching-page-count disagrees with the complete --pages-json observation"
            )
        selected_page_id = normalize_page_id(args.observed_page_id)
        if args.owners_json:
            owners = parse_json_observation(args.owners_json, "--owners-json", owners=True)
        elif len(page_matches["chatgpt_pages"]) == 1:
            owners = [
                {
                    "page_id": selected_page_id,
                    "owner_token": args.observed_tab_owner_token,
                }
            ]
        else:
            raise BridgeError(
                "--owners-json is required when the complete page list contains multiple ChatGPT pages"
            )
        if not any(
            page["page_id"] == selected_page_id
            and page["url"] == args.observed_page_url.strip()
            for page in selected_matches
        ):
            raise BridgeError(
                "Observed pageId and URL are not one matched page in --pages-json"
            )
        resolved = resolve_owned_tab(
            repo,
            claim_token=args.browser_lease_token,
            thread_id=args.bridge_thread_id,
            browser_profile=args.browser_profile,
            expected_project_id=expected_project_id,
            pages=pages,
            owners=owners,
        )
        if resolved.get("action") != "reuse-owned-tab":
            raise BridgeError(
                f"Preflight tab resolution returned {resolved.get('action')!r} "
                f"({resolved.get('reason', 'no-reason')}); HOLD"
            )
        if (
            resolved.get("page_id") != selected_page_id
            or resolved.get("url") != args.observed_page_url.strip()
        ):
            raise BridgeError("Preflight observations do not match the uniquely owned page")
        if args.conversation_bootstrap:
            if expected_conversation_id or observed_conversation_id:
                raise BridgeError("A conversation bootstrap cannot already have a conversation id")
            tab_verification = verify_bootstrap_tab(
                repo,
                claim_token=args.browser_lease_token,
                thread_id=args.bridge_thread_id,
                browser_profile=args.browser_profile,
                expected_project_id=expected_project_id,
                observed_page_url=args.observed_page_url,
                matching_page_count=args.matching_page_count,
                observed_page_id=args.observed_page_id,
                snapshot_page_id=args.snapshot_page_id,
                observed_tab_owner_token=args.observed_tab_owner_token,
                owner_selected_count=resolved.get("owner_selected_count", 0),
            )
            conversation_verification = "bootstrap-pending"
        else:
            if not expected_conversation_id or not observed_conversation_id:
                raise BridgeError("Existing-conversation preflight requires both conversation ids")
            if observed_conversation_id != expected_conversation_id:
                raise BridgeError(
                    f"Observed conversation {observed_conversation_id!r} does not match "
                    f"the reserved conversation {expected_conversation_id!r}; the browser is on the wrong chat"
                )
            tab_verification = verify_claimed_tab(
                repo,
                claim_token=args.browser_lease_token,
                thread_id=args.bridge_thread_id,
                browser_profile=args.browser_profile,
                expected_project_id=expected_project_id,
                expected_conversation_id=expected_conversation_id,
                observed_page_url=args.observed_page_url,
                matching_page_count=args.matching_page_count,
                observed_page_id=args.observed_page_id,
                snapshot_page_id=args.snapshot_page_id,
                observed_tab_owner_token=args.observed_tab_owner_token,
                owner_selected_count=resolved.get("owner_selected_count", 0),
            )
            conversation_verification = "verified"
        lease = tab_verification["claim"]
        observed_page_id = tab_verification["observed_page_id"]
        snapshot_page_id = tab_verification["snapshot_page_id"]
        tab_owner_token = tab_verification["tab_owner_token"]
        assert_preflight_allowed(repo, args.bridge_thread_id, prompt_sha256,
                                 pre_submit_boundary, tab_owner_token)

        print(
            json.dumps(
                {
                    "ready": True,
                    "bridge_thread_id": args.bridge_thread_id,
                    "verified_at": now_iso(),
                    "requested_model": requested,
                    "selected_ui_label": selected,
                    "model_selection_kind": args.model_selection_kind,
                    "model_verification": model_verification,
                    "requested_thinking_intensity": requested_intensity,
                    "selected_thinking_intensity": selected_intensity,
                    "thinking_intensity_verification": "verified",
                    "model_control_receipt": (
                        str(Path(args.model_control_receipt).expanduser().resolve())
                        if args.model_control_receipt
                        else ""
                    ),
                    "model_control_receipt_verification": (
                        "verified" if model_control_receipt else "legacy-not-supplied"
                    ),
                    "source_bundle": str(raw_source.resolve()) if raw_source else "",
                    "staged_bundle": staged_verification or {},
                    "attachment_name": attachment_name,
                    "attachment_sha256": attachment_sha256,
                    "attachment_verification": attachment_verification,
                    "upload_control": upload_control,
                    "upload_action_plan": (
                        str(Path(args.upload_action_plan).expanduser().resolve())
                        if upload_action_plan is not None
                        else ""
                    ),
                    "upload_action_plan_verification": (
                        "verified" if upload_action_plan is not None and upload_receipt is not None else "not-provided"
                    ),
                    "upload_result": (
                        str(Path(args.upload_result).expanduser().resolve())
                        if upload_receipt is not None
                        else ""
                    ),
                    "upload_result_verification": (
                        "verified" if upload_receipt is not None else "not-provided"
                    ),
                    "mcp_host_os": args.mcp_host_os,
                    "browser_host_os": args.browser_host_os,
                    "mcp_temp_root": str(staging_windows_root()),
                    "browser_profile": args.browser_profile.strip(),
                    "expected_project_id": expected_project_id,
                    "observed_project_id": observed_project_id,
                    "project_verification": project_verification,
                    "binding_status": binding_status,
                    "expected_conversation_id": expected_conversation_id,
                    "observed_conversation_id": observed_conversation_id,
                    "conversation_bootstrap": args.conversation_bootstrap,
                    "conversation_verification": conversation_verification,
                    "observed_page_url": args.observed_page_url.strip(),
                    "matching_page_count": tab_verification["matching_page_count"],
                    "owner_selected_count": resolved.get("owner_selected_count", 1),
                    "observed_page_id": observed_page_id,
                    "snapshot_page_id": snapshot_page_id,
                    "tab_owner_token": tab_owner_token,
                    "browser_claim_scope": lease.get("scope", ""),
                    "browser_claim_verification": "verified",
                    "pre_submit_boundary": pre_submit_boundary,
                    "prompt_sha256": prompt_sha256,
                    "turn_boundary_verification": "verified",
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
