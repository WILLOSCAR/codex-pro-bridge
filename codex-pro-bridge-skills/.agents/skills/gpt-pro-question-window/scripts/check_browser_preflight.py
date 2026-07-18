#!/usr/bin/env python3
"""Fail-closed browser observations before a ChatGPT prompt is submitted."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path


SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import BridgeError, file_sha256, now_iso  # noqa: E402
from project_store import REMOTE_PROJECT_ID_RE  # noqa: E402


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify exact visible model and attachment state before browser submission."
    )
    parser.add_argument("--requested-model", required=True)
    parser.add_argument("--selected-ui-label", required=True)
    parser.add_argument("--bundle", default="")
    parser.add_argument("--attachment-name", default="")
    parser.add_argument("--upload-control", default="")
    parser.add_argument("--expected-project-id", default="")
    parser.add_argument("--observed-project-id", default="")
    parser.add_argument("--expected-workspace", default="")
    parser.add_argument("--observed-workspace", default="")
    parser.add_argument("--expected-account-label", default="")
    parser.add_argument("--observed-account-label", default="")
    parser.add_argument("--binding-status", default="")
    args = parser.parse_args()

    try:
        requested = args.requested_model.strip()
        selected = args.selected_ui_label.strip()
        if not requested or not selected:
            raise BridgeError("Requested and selected model labels must both be non-empty")
        if requested != selected:
            raise BridgeError(
                f"Selected UI label {selected!r} does not exactly match requested model {requested!r}"
            )

        raw_bundle = Path(args.bundle).expanduser() if args.bundle else None
        if raw_bundle and not raw_bundle.is_absolute():
            raise BridgeError("--bundle must be an absolute path for Chrome upload")
        bundle_path = raw_bundle.resolve() if raw_bundle else None
        attachment_name = args.attachment_name.strip()
        upload_control = args.upload_control.strip()
        attachment_sha256 = ""
        attachment_verification = "not-required"
        if bundle_path:
            if not bundle_path.is_file():
                raise BridgeError(f"Bundle does not exist: {bundle_path}")
            if attachment_name != bundle_path.name:
                raise BridgeError(
                    f"Visible attachment {attachment_name!r} does not match bundle {bundle_path.name!r}"
                )
            if not upload_control:
                raise BridgeError("--upload-control is required when a bundle is attached")
            if "hidden" in upload_control.lower():
                raise BridgeError("Direct hidden-input clicks are not an accepted upload control")
            attachment_sha256 = file_sha256(bundle_path)
            attachment_verification = "verified"
        elif attachment_name or upload_control:
            raise BridgeError("Attachment observations require --bundle")

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
            raise BridgeError(
                "Standalone submission cannot include Project binding observations"
            )

        print(
            json.dumps(
                {
                    "ready": True,
                    "verified_at": now_iso(),
                    "requested_model": requested,
                    "selected_ui_label": selected,
                    "model_verification": "verified",
                    "attachment_name": attachment_name,
                    "attachment_sha256": attachment_sha256,
                    "attachment_verification": attachment_verification,
                    "upload_control": upload_control,
                    "expected_project_id": expected_project_id,
                    "observed_project_id": observed_project_id,
                    "expected_workspace": expected_workspace,
                    "observed_workspace": observed_workspace,
                    "expected_account_label": expected_account_label,
                    "observed_account_label": observed_account_label,
                    "project_verification": project_verification,
                    "binding_status": binding_status,
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
