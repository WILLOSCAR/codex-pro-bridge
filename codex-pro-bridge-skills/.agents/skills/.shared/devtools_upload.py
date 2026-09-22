"""Guard the one-shot Chrome DevTools file-upload action plan.

The MCP call itself remains owned by the browser-capable Executor.  This module
owns the transport-level invariant around that call so a model cannot turn a
missing chip into a second attachment-button click or a menu-item click.
It cannot intercept the MCP call or inspect an OS-native chooser; an uncertain
result is consequently fail-closed with separate cleanup requirements.
"""

from __future__ import annotations

import hashlib
import json
import ntpath
import re
from collections.abc import Mapping, Sequence
from typing import Any

from bridge_store import BridgeError

UPLOAD_PLAN_V1 = "devtools-upload/v1"
DEVTOOLS_UPLOAD_ROUTE = "chrome-devtools-direct-menu-upload"
_SHA256_RE = re.compile(r"[0-9a-f]{64}")


class NativeChooserRisk(BridgeError):
    """The browser may have opened a native chooser that MCP cannot observe."""

    def __init__(self, reason: str, *, cleanup: Mapping[str, str]) -> None:
        super().__init__(reason)
        self.cleanup = dict(cleanup)


def _text(value: Any, field: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise BridgeError(f"upload plan field {field!r} must be a non-empty string")
    return value.strip()


def _page_id(value: Any) -> str:
    page_id = _text(value, "page_id")
    if not page_id.isdecimal() or int(page_id) <= 0:
        raise BridgeError("upload plan page_id must be a positive integer")
    return str(int(page_id))


def _windows_path(value: Any) -> str:
    path = _text(value, "windows_path")
    if "\n" in path or "\r" in path or not ntpath.isabs(path):
        raise BridgeError("upload plan windows_path must be an absolute Windows path")
    return path


def _sha256(value: Any, field: str = "staged_sha256") -> str:
    digest = _text(value, field).lower()
    if not _SHA256_RE.fullmatch(digest):
        raise BridgeError(f"upload plan {field} must be lowercase SHA-256")
    return digest


def _action(action: Any, index: int) -> Mapping[str, Any]:
    if not isinstance(action, Mapping):
        raise BridgeError(f"upload plan action {index} must be an object")
    return action


def build_upload_action_plan(
    *,
    page_id: str,
    attachment_button_uid: str,
    menu_snapshot_uid: str,
    menu_item_uid: str,
    windows_path: str,
    staged_sha256: str,
) -> dict[str, Any]:
    """Build the only accepted DevTools upload sequence.

    The second snapshot is deliberately part of the plan.  ``upload_file`` must
    target the menu item's UID from that fresh snapshot directly; clicking that
    item is a connector-style action and is forbidden on the DevTools route.
    """

    normalized_page = _page_id(page_id)
    attachment_uid = _text(attachment_button_uid, "attachment_button_uid")
    snapshot_uid = _text(menu_snapshot_uid, "menu_snapshot_uid")
    menu_uid = _text(menu_item_uid, "menu_item_uid")
    if attachment_uid == menu_uid:
        raise BridgeError("attachment button UID and upload menu UID must differ")
    normalized_path = _windows_path(windows_path)
    normalized_digest = _sha256(staged_sha256)
    return {
        "schema_version": UPLOAD_PLAN_V1,
        "route": DEVTOOLS_UPLOAD_ROUTE,
        "page_id": normalized_page,
        "windows_path": normalized_path,
        "staged_sha256": normalized_digest,
        "expected_attachment_name": ntpath.basename(normalized_path),
        "actions": [
            {
                "type": "click",
                "target": "attachment-control",
                "uid": attachment_uid,
                "page_id": normalized_page,
                "count": 1,
            },
            {
                "type": "fresh-snapshot",
                "target": "upload-menu",
                "page_id": normalized_page,
                "snapshot_uid": snapshot_uid,
                "fresh": True,
            },
            {
                "type": "upload_file",
                "target": "from-computer-menu-item",
                "uid": menu_uid,
                "page_id": normalized_page,
                "snapshot_uid": snapshot_uid,
                "windows_path": normalized_path,
            },
        ],
        "verification": {
            "required": "attachment-chip",
            "on_failure": "native-chooser-risk-stop",
        },
    }


def validate_upload_action_plan(plan: Mapping[str, Any]) -> dict[str, Any]:
    """Reject menu clicks, repeated attachment clicks, and stale snapshot UIDs."""

    if not isinstance(plan, Mapping):
        raise BridgeError("DevTools upload action plan must be an object")
    if _text(plan.get("schema_version"), "schema_version") != UPLOAD_PLAN_V1:
        raise BridgeError("Unsupported DevTools upload action plan schema")
    if _text(plan.get("route"), "route") != DEVTOOLS_UPLOAD_ROUTE:
        raise BridgeError("Upload action plan is not the DevTools direct-menu route")
    page_id = _page_id(plan.get("page_id"))
    windows_path = _windows_path(plan.get("windows_path"))
    staged_sha256 = _sha256(plan.get("staged_sha256"))
    expected_name = _text(plan.get("expected_attachment_name"), "expected_attachment_name")
    if expected_name != ntpath.basename(windows_path):
        raise BridgeError("upload plan attachment name does not match Windows path")
    actions = plan.get("actions")
    if not isinstance(actions, Sequence) or isinstance(actions, (str, bytes)):
        raise BridgeError("upload plan actions must be an ordered list")
    if len(actions) != 3:
        raise BridgeError(
            "DevTools upload requires exactly click attachment, fresh snapshot, upload_file"
        )
    click, snapshot, upload = (_action(item, index) for index, item in enumerate(actions))
    if click.get("type") != "click" or click.get("target") != "attachment-control":
        raise BridgeError("first DevTools upload action must click the attachment control")
    if _page_id(click.get("page_id")) != page_id:
        raise BridgeError("attachment click page_id does not match upload page")
    if click.get("count") != 1:
        raise BridgeError("attachment control must be clicked exactly once")
    attachment_uid = _text(click.get("uid"), "attachment click uid")
    if snapshot.get("type") != "fresh-snapshot" or snapshot.get("target") != "upload-menu":
        raise BridgeError("second DevTools upload action must be a fresh upload-menu snapshot")
    if _page_id(snapshot.get("page_id")) != page_id:
        raise BridgeError("fresh upload-menu snapshot page_id does not match upload page")
    if snapshot.get("fresh") is not True:
        raise BridgeError("upload-menu snapshot must be fresh and taken after the attachment click")
    snapshot_uid = _text(snapshot.get("snapshot_uid"), "snapshot_uid")
    if upload.get("type") != "upload_file" or upload.get("target") != "from-computer-menu-item":
        raise BridgeError(
            "DevTools must call upload_file directly on the from-computer menu item"
        )
    if _page_id(upload.get("page_id")) != page_id:
        raise BridgeError("upload_file page_id does not match upload page")
    if _text(upload.get("snapshot_uid"), "upload snapshot_uid") != snapshot_uid:
        raise BridgeError("upload_file must use the fresh upload-menu snapshot UID")
    menu_uid = _text(upload.get("uid"), "upload menu UID")
    if menu_uid == attachment_uid:
        raise BridgeError("upload_file UID must be the menu item, not the attachment control")
    if _text(upload.get("windows_path"), "upload windows_path") != windows_path:
        raise BridgeError("upload_file path does not match the verified staged path")
    if click.get("menu_item_uid") or upload.get("click"):
        raise BridgeError("DevTools upload plan must not click the upload menu item")
    verification = plan.get("verification")
    if not isinstance(verification, Mapping):
        raise BridgeError("upload plan verification must be an object")
    if _text(verification.get("required"), "verification.required") != "attachment-chip":
        raise BridgeError("DevTools upload requires attachment-chip verification")
    if _text(verification.get("on_failure"), "verification.on_failure") != "native-chooser-risk-stop":
        raise BridgeError("upload failure must stop with native-chooser-risk handling")
    return {
        "schema_version": UPLOAD_PLAN_V1,
        "route": DEVTOOLS_UPLOAD_ROUTE,
        "page_id": page_id,
        "windows_path": windows_path,
        "staged_sha256": staged_sha256,
        "expected_attachment_name": expected_name,
        "actions": [dict(_action(item, index)) for index, item in enumerate(actions)],
        "verification": dict(verification),
    }


def upload_action_plan_sha256(plan: Mapping[str, Any]) -> str:
    """Return the digest of the canonical, validated action plan."""

    normalized = validate_upload_action_plan(plan)
    payload = json.dumps(
        normalized,
        ensure_ascii=False,
        sort_keys=True,
        separators=(",", ":"),
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _native_chooser_cleanup() -> dict[str, str]:
    return {
        "browser_ui": "not-proven-clean; do not claim native chooser closure",
        "native_chooser": "possibly-open-or-unknown; obtain an explicit same-host observation or user confirmation",
        "staged_file": "clean separately with manage_browser_staging.py cleanup and the verified staged SHA-256",
        "send": "forbidden until a fresh successful upload/preflight; never resend or retry upload blindly",
        "safety": "never invoke a global Windows dialog killer; limit cleanup to this exact chooser/page",
    }


def verify_upload_result(
    plan: Mapping[str, Any],
    result: Mapping[str, Any],
) -> dict[str, Any]:
    """Require an accepted MCP upload and an observed attachment chip.

    A failure intentionally raises ``NativeChooserRisk``.  MCP may not be able to
    distinguish a rejected upload from a still-open native chooser, so the caller
    must stop and report that uncertainty rather than click again.
    """

    normalized = validate_upload_action_plan(plan)
    if not isinstance(result, Mapping):
        raise NativeChooserRisk(
            "DevTools upload result is not an object; a native file chooser may still be open",
            cleanup=_native_chooser_cleanup(),
        )
    status = result.get("status")
    if (
        result.get("isError") is True
        or result.get("error")
        or status not in {"accepted", "success"}
        or result.get("attachment_chip") is not True
    ):
        reason = (
            "DevTools upload failed or the attachment chip is absent; a native file chooser "
            "may still be open. Stop all further upload and Send operations."
        )
        raise NativeChooserRisk(reason, cleanup=_native_chooser_cleanup())
    observed_name = result.get("attachment_name", normalized["expected_attachment_name"])
    if observed_name != normalized["expected_attachment_name"]:
        raise NativeChooserRisk(
            "DevTools upload reported an attachment name different from the verified staged file",
            cleanup=_native_chooser_cleanup(),
        )
    if result.get("page_id") is not None:
        try:
            observed_page = _page_id(result["page_id"])
        except BridgeError as exc:
            raise NativeChooserRisk(
                "DevTools upload reported an invalid page identity",
                cleanup=_native_chooser_cleanup(),
            ) from exc
        if observed_page != normalized["page_id"]:
            raise NativeChooserRisk(
                "DevTools upload reported a different page than the verified plan",
                cleanup=_native_chooser_cleanup(),
            )
    if result.get("windows_path") is not None and result["windows_path"] != normalized["windows_path"]:
        raise NativeChooserRisk(
            "DevTools upload reported a path different from the verified staged file",
            cleanup=_native_chooser_cleanup(),
        )
    if result.get("staged_sha256") is not None:
        try:
            observed_digest = _sha256(result["staged_sha256"], "result.staged_sha256")
        except BridgeError as exc:
            raise NativeChooserRisk(
                "DevTools upload reported an invalid staged SHA-256",
                cleanup=_native_chooser_cleanup(),
            ) from exc
        if observed_digest != normalized["staged_sha256"]:
            raise NativeChooserRisk(
                "DevTools upload reported a digest different from the verified staged file",
                cleanup=_native_chooser_cleanup(),
            )
    if (
        result.get("upload_action_plan_sha256") is not None
        and result["upload_action_plan_sha256"] != upload_action_plan_sha256(normalized)
    ):
        raise NativeChooserRisk(
            "DevTools upload reported a different action plan",
            cleanup=_native_chooser_cleanup(),
        )
    plan_sha256 = upload_action_plan_sha256(normalized)
    return {
        "status": "accepted",
        "route": DEVTOOLS_UPLOAD_ROUTE,
        "upload_action_plan_sha256": plan_sha256,
        "page_id": normalized["page_id"],
        "windows_path": normalized["windows_path"],
        "attachment_name": normalized["expected_attachment_name"],
        "staged_sha256": normalized["staged_sha256"],
        "attachment_chip": True,
        "native_chooser": "not-observed",
        "staged_cleanup": "separate-exact-file-cleanup-required",
        "next_action": "fresh-snapshot-and-browser-preflight",
    }


def validate_upload_receipt(
    plan: Mapping[str, Any],
    receipt: Mapping[str, Any],
) -> dict[str, Any]:
    """Validate the persisted successful receipt against one exact plan."""

    normalized = validate_upload_action_plan(plan)
    if not isinstance(receipt, Mapping):
        raise BridgeError("DevTools upload receipt must be an object")
    if receipt.get("status") != "accepted":
        raise BridgeError("DevTools upload receipt is not a successful accepted result")
    if receipt.get("isError") is True or receipt.get("error"):
        raise BridgeError("DevTools upload receipt reports an error")
    if receipt.get("route") != DEVTOOLS_UPLOAD_ROUTE:
        raise BridgeError("DevTools upload receipt route does not match the action plan")
    expected_plan_sha = upload_action_plan_sha256(normalized)
    if receipt.get("upload_action_plan_sha256") != expected_plan_sha:
        raise BridgeError("DevTools upload receipt does not bind the same action plan")
    try:
        receipt_page = _page_id(receipt.get("page_id"))
    except BridgeError as exc:
        raise BridgeError("DevTools upload receipt page_id is invalid") from exc
    if receipt_page != normalized["page_id"]:
        raise BridgeError("DevTools upload receipt page_id does not match the action plan")
    if receipt.get("windows_path") != normalized["windows_path"]:
        raise BridgeError("DevTools upload receipt path does not match the action plan")
    try:
        receipt_digest = _sha256(receipt.get("staged_sha256"), "receipt.staged_sha256")
    except BridgeError as exc:
        raise BridgeError("DevTools upload receipt staged SHA-256 is invalid") from exc
    if receipt_digest != normalized["staged_sha256"]:
        raise BridgeError("DevTools upload receipt digest does not match the action plan")
    if receipt.get("attachment_name") != normalized["expected_attachment_name"]:
        raise BridgeError("DevTools upload receipt attachment name does not match the action plan")
    if receipt.get("attachment_chip") is not True:
        raise BridgeError("DevTools upload receipt does not prove an attachment chip")
    if receipt.get("native_chooser") != "not-observed":
        raise BridgeError("DevTools upload receipt has unresolved native chooser state")
    return {
        "status": "accepted",
        "route": DEVTOOLS_UPLOAD_ROUTE,
        "upload_action_plan_sha256": expected_plan_sha,
        "page_id": normalized["page_id"],
        "windows_path": normalized["windows_path"],
        "attachment_name": normalized["expected_attachment_name"],
        "staged_sha256": normalized["staged_sha256"],
        "attachment_chip": True,
        "native_chooser": "not-observed",
        "staged_cleanup": "separate-exact-file-cleanup-required",
        "next_action": "fresh-snapshot-and-browser-preflight",
    }


def native_chooser_cleanup_requirements() -> dict[str, str]:
    """Return the safe, non-global cleanup contract for a failed upload."""

    return _native_chooser_cleanup()
