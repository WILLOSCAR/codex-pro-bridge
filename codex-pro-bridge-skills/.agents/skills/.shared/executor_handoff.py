"""Validation helpers for the versioned Bridge Executor handoff contract.

The JSON document remains the transport format, while this module owns the
small structural gate used by deterministic preparation and its tests.  Browser
identity, attempts, and model observations remain owned by their existing
helpers.
"""

from __future__ import annotations

from collections.abc import Mapping
from pathlib import Path
from typing import Any

from bridge_store import BridgeError, validate_id
from model_controls import MODEL_SELECTION_KINDS

HANDOFF_V1 = "executor_handoff/v1"
HANDOFF_V2 = "executor_handoff/v2"
CONTEXT_POLICIES = ("auto", "explicit", "none")
ATTACHMENT_POLICIES = ("bundle", "none")
EXTERNAL_ACTIONS = frozenset(
    {
        "verify-project-identity",
        "upload-task-bundle",
        "send-once",
        "capture-reply",
    }
)


def _required_string(data: Mapping[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise BridgeError(f"handoff field {field!r} must be a non-empty string")
    return value.strip()


def validate_handoff(
    handoff: Mapping[str, Any],
    *,
    require_new_round: bool = False,
) -> dict[str, Any]:
    """Validate the transport-level handoff and return a normalized copy.

    ``v1`` remains readable only for recovery.  A new prepare-and-run round
    must carry the v2 model and Project controls so a parent cannot silently
    omit them while still producing a sendable file.
    """

    if not isinstance(handoff, Mapping):
        raise BridgeError("executor handoff must be a JSON object")
    data = dict(handoff)
    schema = _required_string(data, "schema_version")
    if schema not in {HANDOFF_V1, HANDOFF_V2}:
        raise BridgeError(f"unsupported executor handoff schema: {schema}")
    mode = _required_string(data, "mode")
    if mode not in {"prepare-and-run", "recover-only"}:
        raise BridgeError("handoff mode must be prepare-and-run or recover-only")
    for field in (
        "repo",
        "bridge_thread_id",
        "request_file",
        "request_sha256",
        "expected_output_dir",
    ):
        _required_string(data, field)
    repo = Path(data["repo"]).expanduser()
    if not repo.is_absolute():
        raise BridgeError("handoff repo must be an absolute path")
    repo = repo.resolve()
    for field in ("request_file", "expected_output_dir"):
        path = Path(data[field]).expanduser()
        if not path.is_absolute():
            raise BridgeError(f"handoff {field} must be an absolute path")
        if not path.resolve().is_relative_to(repo):
            raise BridgeError(f"handoff {field} must stay inside handoff repo")
    validate_id(data["bridge_thread_id"], "bridge thread id")
    if len(data["request_sha256"]) != 64 or any(
        char not in "0123456789abcdef" for char in data["request_sha256"].lower()
    ):
        raise BridgeError("handoff request_sha256 must be lowercase SHA-256")
    if not isinstance(data.get("allow_send"), bool):
        raise BridgeError("handoff allow_send must be boolean")
    actions = data.get("allowed_external_actions")
    if not isinstance(actions, list) or any(
        not isinstance(item, str) or not item.strip() for item in actions
    ):
        raise BridgeError("handoff allowed_external_actions must be a string list")
    if mode == "prepare-and-run":
        if "attempt_id" in data and str(data.get("attempt_id", "")).strip():
            raise BridgeError("prepare-and-run handoff must not include attempt_id")
        if require_new_round and schema != HANDOFF_V2:
            raise BridgeError(
                "executor_handoff/v1 is recover-only; new rounds require executor_handoff/v2"
            )
        if schema == HANDOFF_V1:
            raise BridgeError("executor_handoff/v1 is recover-only compatible")
        if len(actions) != len(set(actions)):
            raise BridgeError("handoff allowed_external_actions must not contain duplicates")
        unknown_actions = set(actions) - EXTERNAL_ACTIONS
        if unknown_actions:
            raise BridgeError(
                "handoff allowed_external_actions contains unknown actions: "
                + ", ".join(sorted(unknown_actions))
            )
        if not data.get("allow_send"):
            raise BridgeError("prepare-and-run requires explicit allow_send=true")
        for field in (
            "requested_model",
            "requested_thinking_intensity",
            "binding_action",
            "context_policy",
            "attachment_policy",
        ):
            _required_string(data, field)
        target_url = data.get("target_project_url", "")
        if not isinstance(target_url, str):
            raise BridgeError("handoff target_project_url must be a string")
        kind = _required_string(data, "model_selection_kind")
        if kind not in MODEL_SELECTION_KINDS:
            raise BridgeError(
                "handoff model_selection_kind must be one of: "
                + ", ".join(MODEL_SELECTION_KINDS)
            )
        if data["binding_action"] not in {
            "none",
            "reuse",
            "verify",
            "rebind-and-verify",
        }:
            raise BridgeError("handoff binding_action is invalid")
        local_project_id = str(data.get("bridge_project_id", "")).strip()
        remote_project_id = str(data.get("remote_project_id", "")).strip()
        if bool(local_project_id) != bool(remote_project_id):
            raise BridgeError(
                "Project handoff must include both bridge_project_id and remote_project_id"
            )
        if data["binding_action"] == "none" and (
            local_project_id or remote_project_id or target_url.strip()
        ):
            raise BridgeError("Standalone handoff cannot include Project identity")
        if data["binding_action"] != "none" and (
            not local_project_id or not remote_project_id or not target_url.strip()
        ):
            raise BridgeError(
                "Project-bound prepare-and-run handoff requires Project identity and target_project_url"
            )
        context_policy = data["context_policy"]
        if context_policy not in CONTEXT_POLICIES:
            raise BridgeError(
                "handoff context_policy must be one of: "
                + ", ".join(CONTEXT_POLICIES)
            )
        attachment_policy = data["attachment_policy"]
        if attachment_policy not in ATTACHMENT_POLICIES:
            raise BridgeError(
                "handoff attachment_policy must be one of: "
                + ", ".join(ATTACHMENT_POLICIES)
            )
        max_files = data.get("max_files")
        if isinstance(max_files, bool) or not isinstance(max_files, int):
            raise BridgeError("handoff max_files must be an integer")
        if max_files < 0:
            raise BridgeError("handoff max_files must not be negative")
        if context_policy == "none":
            if max_files != 0:
                raise BridgeError("handoff context_policy none requires max_files=0")
            if attachment_policy != "none":
                raise BridgeError(
                    "handoff context_policy none requires attachment_policy none"
                )
            if "upload-task-bundle" in actions:
                raise BridgeError(
                    "handoff context_policy none cannot authorize upload-task-bundle"
                )
        else:
            if max_files <= 0:
                raise BridgeError(
                    "handoff max_files must be positive for repository context"
                )
            if attachment_policy != "bundle":
                raise BridgeError(
                    "repository context requires attachment_policy bundle"
                )
            if "upload-task-bundle" not in actions:
                raise BridgeError(
                    "repository context requires upload-task-bundle authorization"
                )
        for required_action in ("send-once", "capture-reply"):
            if required_action not in actions:
                raise BridgeError(
                    f"prepare-and-run handoff must authorize {required_action}"
                )
    else:
        attempt_id = _required_string(data, "attempt_id")
        validate_id(attempt_id, "attempt id")
        if data.get("allow_send"):
            raise BridgeError("recover-only handoff cannot authorize Send")
    if data.get("business_deadline") is not None and not isinstance(
        data.get("business_deadline"), str
    ):
        raise BridgeError("business_deadline must be an ISO string or null")
    return data
