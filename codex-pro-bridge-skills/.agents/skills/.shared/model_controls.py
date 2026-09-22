"""Shared validation for ChatGPT model-control observations and action budgets."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from bridge_store import BridgeError

MODEL_SELECTION_KINDS = ("exact", "latest-alias")
MODEL_CONTROL_TRACE_V1 = "model-controls/v1"


def assess_model_selection(requested: str, selected: str, kind: str) -> str:
    """Classify a checked model item without resolving dynamic UI aliases."""
    requested = requested.strip()
    selected = selected.strip()
    if not requested or not selected:
        return "unverified"
    if requested != selected:
        return "mismatch"
    if kind == "latest-alias":
        return "alias-selected"
    return "verified"


def _required_text(data: Mapping[str, Any], field: str) -> str:
    value = data.get(field)
    if not isinstance(value, str) or not value.strip():
        raise BridgeError(f"model-control field {field!r} must be a non-empty string")
    return value.strip()


def _count(counts: Mapping[str, Any], field: str) -> int:
    value = counts.get(field)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise BridgeError(f"model-control count {field!r} must be a non-negative integer")
    return value


def validate_model_control_trace(trace: Mapping[str, Any]) -> dict[str, Any]:
    """Validate one bounded pre-Send model/thinking control transaction.

    The trace deliberately distinguishes the one combined initial read, bounded
    adjustment progress reads, and one combined final confirmation.  Re-reading
    the full model/thinking pair after preflight is forbidden.
    """

    if not isinstance(trace, Mapping):
        raise BridgeError("model-control trace must be an object")
    if _required_text(trace, "schema_version") != MODEL_CONTROL_TRACE_V1:
        raise BridgeError("unsupported model-control trace schema")
    page_id = _required_text(trace, "page_id")
    if not page_id.isdecimal() or int(page_id) <= 0:
        raise BridgeError("model-control page_id must be a positive integer")
    page_id = str(int(page_id))
    owner = _required_text(trace, "tab_owner_token")
    page_url = _required_text(trace, "observed_page_url")
    if not page_url.startswith("https://chatgpt.com/"):
        raise BridgeError("model-control observed_page_url must be an https ChatGPT URL")
    requested_model = _required_text(trace, "requested_model")
    selected_model = _required_text(trace, "selected_model")
    kind = _required_text(trace, "model_selection_kind")
    if kind not in MODEL_SELECTION_KINDS:
        raise BridgeError("model-control selection kind is invalid")
    if assess_model_selection(requested_model, selected_model, kind) in {
        "unverified",
        "mismatch",
    }:
        raise BridgeError("final model selection does not match the request")
    requested_intensity = _required_text(trace, "requested_thinking_intensity")
    selected_intensity = _required_text(trace, "selected_thinking_intensity")
    if selected_intensity != requested_intensity:
        raise BridgeError("final thinking intensity does not match the request")
    initial_model = _required_text(trace, "initial_model")
    initial_intensity = _required_text(trace, "initial_thinking_intensity")
    counts = trace.get("counts")
    if not isinstance(counts, Mapping):
        raise BridgeError("model-control counts must be an object")
    normalized_counts = {
        field: _count(counts, field)
        for field in (
            "combined_initial_read",
            "model_menu_open",
            "model_selection",
            "thinking_control_open",
            "thinking_adjustment",
            "thinking_progress_read",
            "combined_final_confirmation",
            "post_preflight_recheck",
        )
    }
    if normalized_counts["combined_initial_read"] != 1:
        raise BridgeError("model controls require exactly one combined initial read")
    if normalized_counts["combined_final_confirmation"] != 1:
        raise BridgeError("model controls require exactly one combined final confirmation")
    if normalized_counts["post_preflight_recheck"] != 0:
        raise BridgeError("model controls must not be rechecked after successful preflight")
    initial_model_matches = assess_model_selection(
        requested_model, initial_model, kind
    ) not in {"unverified", "mismatch"}
    model_expected = 0 if initial_model_matches else 1
    if normalized_counts["model_menu_open"] != model_expected:
        raise BridgeError("model menu open count does not match the initial model state")
    if normalized_counts["model_selection"] != model_expected:
        raise BridgeError("model selection count does not match the initial model state")
    if initial_intensity == requested_intensity:
        if any(
            normalized_counts[field] != 0
            for field in (
                "thinking_control_open",
                "thinking_adjustment",
                "thinking_progress_read",
            )
        ):
            raise BridgeError("matching thinking intensity must not be adjusted")
    else:
        if normalized_counts["thinking_control_open"] != 1:
            raise BridgeError("thinking control may be opened only once when adjustment is needed")
        adjustments = normalized_counts["thinking_adjustment"]
        if not 1 <= adjustments <= 6:
            raise BridgeError("thinking adjustment count must be between 1 and 6")
        if normalized_counts["thinking_progress_read"] != adjustments:
            raise BridgeError("each thinking adjustment requires one progress read")
    return {
        "schema_version": MODEL_CONTROL_TRACE_V1,
        "page_id": page_id,
        "tab_owner_token": owner,
        "observed_page_url": page_url,
        "requested_model": requested_model,
        "selected_model": selected_model,
        "model_selection_kind": kind,
        "requested_thinking_intensity": requested_intensity,
        "selected_thinking_intensity": selected_intensity,
        "initial_model": initial_model,
        "initial_thinking_intensity": initial_intensity,
        "counts": normalized_counts,
        "status": "verified",
        "next_action": "fresh-browser-preflight-without-control-recheck",
    }
