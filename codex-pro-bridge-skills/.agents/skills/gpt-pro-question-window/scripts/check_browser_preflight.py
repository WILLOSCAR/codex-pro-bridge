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


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Verify exact visible model and attachment state before browser submission."
    )
    parser.add_argument("--requested-model", required=True)
    parser.add_argument("--selected-ui-label", required=True)
    parser.add_argument("--bundle", default="")
    parser.add_argument("--attachment-name", default="")
    parser.add_argument("--upload-control", default="")
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
