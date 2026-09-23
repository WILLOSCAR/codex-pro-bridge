#!/usr/bin/env python3
"""Capture one GPT Pro exchange and optionally record an immediate verdict."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse

SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_attempts import (
    active_attempt,
    complete_attempt,
    reserve_capture,
    validate_capture,
)
from bridge_store import (
    DEFAULT_BROWSER_PROFILE,
    BridgeError,
    append_event,
    atomic_write_text,
    bridge_root,
    default_codex_session_id,
    default_gpt_session_id,
    file_lock,
    file_sha256,
    load_events,
    now_iso,
    parse_metadata,
    record_codex_verdict,
    repo_relative,
    resolve_repo_path,
    validate_id,
    write_bound_metadata,
    write_session_index,
)
from browser_identity import (
    normalize_page_id,
    resolve_owned_tab,
    verify_claimed_tab,
)
from browser_observations import parse_json_observation
from model_controls import MODEL_SELECTION_KINDS, assess_model_selection
from project_store import BridgeProjectStore


def read_value(text: str, file_path: str) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    return (text or "").strip()


def slugify(value: str, fallback: str = "turn") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return (slug or fallback)[:80].strip("-") or fallback


def one_line(value: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text[: limit - 3].rstrip() + "..." if len(text) > limit else text or "-"


def next_turn_number(session_dir: Path) -> int:
    numbers = []
    for path in session_dir.glob("*.md"):
        match = re.match(r"^(\d+)-", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def validate_web_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise BridgeError("GPT Pro conversation URL must be an https URL")
    hostname = (parsed.hostname or "").lower()
    allowed = hostname in {"chatgpt.com", "chat.openai.com"} or hostname.endswith(
        (".chatgpt.com", ".chat.openai.com")
    )
    if not allowed:
        raise BridgeError("GPT Pro URL must point to a ChatGPT conversation")
    parts = [part for part in parsed.path.split("/") if part]
    standalone_conversation = (
        len(parts) == 2 and parts[0] == "c" and bool(parts[1])
    )
    project_conversation = (
        len(parts) == 4
        and parts[0] == "g"
        and bool(parts[1])
        and parts[2] == "c"
        and bool(parts[3])
    )
    if not (standalone_conversation or project_conversation):
        raise BridgeError("GPT Pro URL must identify a conversation")
    return value


def project_segment_from_conversation_url(value: str) -> str:
    parts = [part for part in urlparse(value).path.split("/") if part]
    return parts[1] if len(parts) == 4 and parts[0] == "g" and parts[2] == "c" else ""


def conversation_id_from_url(value: str) -> str:
    """Return the ChatGPT conversation id from a /c/<id> or /g/<pid>/c/<id> URL."""
    parts = [part for part in urlparse(value).path.split("/") if part]
    if len(parts) == 2 and parts[0] == "c":
        return parts[1]
    if len(parts) == 4 and parts[0] == "g" and parts[2] == "c":
        return parts[3]
    return ""


def validate_timestamp(value: str, flag: str, *, default_now: bool = False) -> str:
    if not value:
        return now_iso() if default_now else ""
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise BridgeError(f"{flag} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise BridgeError(f"{flag} must include a timezone offset")
    return value


def timestamp_value(value: str) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def build_turn(
    *,
    number: int,
    title: str,
    thread_id: str,
    bridge_project_id: str,
    remote_project_id: str,
    observed_workspace: str,
    observed_account_label: str,
    gpt_session_id: str,
    codex_session_id: str,
    web_url: str,
    web_title: str,
    codex_notes: str,
    bundle_path: str,
    bundle_sha256: str,
    submitted_at: str,
    generation_observed_at: str,
    response_completed_at: str,
    response_wait_seconds: int | None,
    requested_model: str,
    selected_ui_label: str,
    model_selection_kind: str,
    model_verification: str,
    requested_thinking_intensity: str,
    selected_thinking_intensity: str,
    thinking_intensity_verification: str,
    attachment_name: str,
    attachment_verification: str,
    upload_control: str,
    capture_route: str,
    answer_format: str,
    answer_sha256: str,
    remote_turn_id: str,
    saved_at: str,
    prompt: str,
    answer: str,
    capture_summary: str,
    capture_fingerprint: str,
) -> str:
    number_text = f"{number:03d}"
    return "\n".join(
        [
            f"# {number_text} {title}",
            "",
            "## Metadata",
            f"- Bridge Thread ID: `{thread_id}`",
            f"- Bridge Project ID: `{bridge_project_id or '-'}`",
            f"- ChatGPT Project ID: `{remote_project_id or '-'}`",
            f"- Observed workspace: `{observed_workspace or '-'}`",
            f"- Observed account: `{observed_account_label or '-'}`",
            f"- GPT Pro Session ID: `{gpt_session_id}`",
            f"- Codex Session ID: `{codex_session_id}`",
            f"- GPT Pro URL: {web_url}",
            f"- Web Title: {web_title or '-'}",
            f"- Codex Notes: {codex_notes or '-'}",
            f"- Bundle: {bundle_path or '-'}",
            f"- Bundle SHA-256: {bundle_sha256 or '-'}",
            f"- Capture Fingerprint: {capture_fingerprint}",
            f"- Requested Model: {requested_model or '-'}",
            f"- Selected UI Label: {selected_ui_label or '-'}",
            f"- Model Selection Kind: {model_selection_kind}",
            f"- Model Verification: {model_verification}",
            f"- Requested Thinking Intensity: {requested_thinking_intensity or '-'}",
            f"- Selected Thinking Intensity: {selected_thinking_intensity or '-'}",
            f"- Thinking Intensity Verification: {thinking_intensity_verification}",
            f"- Attachment Name: {attachment_name or '-'}",
            f"- Attachment Verification: {attachment_verification}",
            f"- Upload Control: {upload_control or '-'}",
            f"- Capture Route: {capture_route}",
            f"- Answer Format: {answer_format}",
            f"- Answer SHA-256: {answer_sha256}",
            f"- Remote Turn ID: {remote_turn_id or '-'}",
            f"- Submitted at: {submitted_at}",
            f"- Generation observed at: {generation_observed_at or '-'}",
            f"- Response completed at: {response_completed_at or '-'}",
            f"- Response wait seconds: {response_wait_seconds if response_wait_seconds is not None else '-'}",
            f"- Captured at: {saved_at}",
            "",
            "## Prompt",
            "",
            prompt,
            "",
            "## Evidence Contract",
            "",
            f"- Bundle: {bundle_path or '-'}",
            f"- Bundle SHA-256: {bundle_sha256 or '-'}",
            f"- Codex notes: {codex_notes or '-'}",
            "- GPT Pro saw only the prompt and attached or pasted evidence.",
            "- The local repository remains the source of truth.",
            "",
            "## GPT Pro Answer",
            "",
            answer,
            "",
            "## Capture Summary",
            "",
            capture_summary or "_Pending Codex verdict._",
            "",
            "_Codex verification is stored as a later immutable verdict artifact and timeline event._",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a GPT Pro answer as an immutable exchange.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--bridge-thread-id", required=True, help="Canonical task id.")
    parser.add_argument("--bridge-project-id", default="", help="Optional parent Bridge Project.")
    parser.add_argument(
        "--standalone",
        action="store_true",
        help=(
            "Fast lane for high-frequency Review Probes: capture a standalone turn "
            "with no Bridge Project. Skips all Project source-sync gating. Fails if "
            "the thread is attached to a Project."
        ),
    )
    parser.add_argument(
        "--single-round",
        action="store_true",
        help="Refuse to capture if this thread already has a gpt-exchange (one-shot probe).",
    )
    parser.add_argument(
        "--expected-conversation-id",
        default="",
        help="Canonical ChatGPT conversation id reserved for this thread; the bound "
        "web URL must match it. Prevents 'right Project, wrong chat'.",
    )
    parser.add_argument(
        "--browser-lease-token",
        default="",
        help=(
            "Conversation-scoped claim token verified during browser fallback. "
            "Omit for native read_thread capture after the Send claim was released."
        ),
    )
    parser.add_argument("--browser-profile", default=DEFAULT_BROWSER_PROFILE)
    parser.add_argument("--observed-page-url", default="")
    parser.add_argument("--matching-page-count", type=int, default=0)
    parser.add_argument("--pages-json", default="")
    parser.add_argument("--owners-json", default="")
    parser.add_argument("--observed-page-id", default="")
    parser.add_argument("--snapshot-page-id", default="")
    parser.add_argument("--observed-tab-owner-token", default="")
    parser.add_argument(
        "--remote-project-id",
        default="",
        help="Observed ChatGPT Project id for a Project-bound conversation.",
    )
    parser.add_argument("--observed-workspace", default="")
    parser.add_argument("--observed-account-label", default="")
    parser.add_argument("--codex-session-id", default="", help="Defaults to <bridge-thread-id>-codex.")
    parser.add_argument("--codex-notes", default="", help="Defaults to the current Codex notes for this thread.")
    parser.add_argument("--gpt-pro-session-id", "--session-id", dest="gpt_pro_session_id", default="", help="Defaults to <bridge-thread-id>-gpt-pro.")
    parser.add_argument("--web-url", default="", help="Required when creating a GPT Pro session.")
    parser.add_argument("--web-title", default="")
    parser.add_argument("--purpose", default="")
    parser.add_argument("--turn-title", default="")
    parser.add_argument("--bundle", default="", help="Existing bundle under the repository root that was actually sent.")
    parser.add_argument(
        "--asked-at",
        "--submitted-at",
        dest="submitted_at",
        default="",
        help="Observed submission time as ISO-8601 with timezone.",
    )
    parser.add_argument("--generation-observed-at", default="", help="First observed generating state.")
    parser.add_argument("--attempt-id", default="", help="Capture the pinned durable submission attempt")
    parser.add_argument("--response-completed-at", default="", help="Observed completed response time.")
    parser.add_argument("--requested-model", default="", help="Exact model label required by the task.")
    parser.add_argument("--selected-ui-label", default="", help="Exact checked model label visible before submission.")
    parser.add_argument(
        "--model-selection-kind",
        choices=MODEL_SELECTION_KINDS,
        default="exact",
        help="Use latest-alias when the checked UI item is a dynamic label such as 最新.",
    )
    parser.add_argument("--requested-thinking-intensity", default="", help="Exact thinking intensity required by the task.")
    parser.add_argument("--selected-thinking-intensity", default="", help="Exact selected thinking intensity visible before submission.")
    parser.add_argument(
        "--attachment-name",
        default="",
        help="Attachment name visible in the composer; with --attempt-id it must match canonical preflight.",
    )
    parser.add_argument("--upload-control", default="", help="Successful semantic upload route, for example visible-menu.")
    parser.add_argument(
        "--capture-route",
        choices=("browser", "browser-fallback", "native-read-thread"),
        default="browser",
        help=(
            "Where the full raw answer was captured. browser is the compatible "
            "legacy path; browser-fallback requires a pinned turn and live new lease."
        ),
    )
    parser.add_argument(
        "--answer-format",
        choices=("copied-markdown", "native-raw", "plain-text-degraded"),
        default="",
        help=(
            "Serialization of the saved answer. Browser fallback should use "
            "copied-markdown from ChatGPT's visible Copy reply control."
        ),
    )
    parser.add_argument(
        "--remote-turn-id",
        default="",
        help="Exact completed ChatGPT turn selected by native response retrieval.",
    )
    parser.add_argument("--prompt", default="")
    parser.add_argument("--prompt-file", default="")
    parser.add_argument("--answer", default="")
    parser.add_argument("--answer-file", default="")
    parser.add_argument("--summary", default="", help="Optional capture summary; also used by legacy immediate-verdict calls.")
    parser.add_argument("--summary-file", default="")
    parser.add_argument("--verification", default="", help="Compatibility path: record an immediate Codex verdict after capture.")
    parser.add_argument("--verification-file", default="")
    parser.add_argument("--decision-trail", default="")
    parser.add_argument("--decision-trail-file", default="")
    args = parser.parse_args()

    try:
        repo = Path(args.repo).resolve()
        if not repo.is_dir():
            raise BridgeError(f"Repository root is not a directory: {repo}")
        thread_id = validate_id(args.bridge_thread_id, "bridge thread id")
        codex_session_id = validate_id(
            args.codex_session_id or default_codex_session_id(thread_id), "Codex session id"
        )
        gpt_session_id = validate_id(
            args.gpt_pro_session_id or default_gpt_session_id(thread_id), "GPT Pro session id"
        )
        prompt = read_value(args.prompt, args.prompt_file)
        answer = read_value(args.answer, args.answer_file)
        summary = read_value(args.summary, args.summary_file)
        verification = read_value(args.verification, args.verification_file)
        decision_trail = read_value(args.decision_trail, args.decision_trail_file)
        if not prompt or not answer:
            raise BridgeError("Both prompt and full GPT Pro answer are required")
        pending_attempt = active_attempt(repo, thread_id)
        attempt_id = args.attempt_id or (pending_attempt["attempt_id"] if pending_attempt else "")
        attempt = None
        if attempt_id:
            prompt = Path(args.prompt_file).read_text(encoding="utf-8") if args.prompt_file else args.prompt
            answer = Path(args.answer_file).read_text(encoding="utf-8") if args.answer_file else args.answer
            attempt = validate_capture(repo, thread_id, attempt_id, prompt=prompt,
                                       conversation_url=args.web_url,
                                       remote_turn_id=args.remote_turn_id.strip())
            if not args.response_completed_at or args.capture_route == "browser":
                raise BridgeError("Attempt capture requires observed completion and a pinned native/browser-fallback route")
            if args.submitted_at and attempt["submitted_at"] and args.submitted_at != attempt["submitted_at"]:
                raise BridgeError("Capture submission time disagrees with checkpoint")
            args.submitted_at = args.submitted_at or attempt["submitted_at"]
        if decision_trail and not verification:
            raise BridgeError("An immediate decision trail requires Codex verification")
        submitted_at = validate_timestamp(
            args.submitted_at, "--submitted-at", default_now=not bool(attempt)
        )
        generation_observed_at = validate_timestamp(
            args.generation_observed_at, "--generation-observed-at"
        )
        response_completed_at = validate_timestamp(
            args.response_completed_at, "--response-completed-at"
        )
        saved_at = now_iso()
        submitted_value = timestamp_value(submitted_at)
        generation_value = timestamp_value(generation_observed_at)
        completed_value = timestamp_value(response_completed_at)
        if generation_value and submitted_value and generation_value < submitted_value:
            raise BridgeError("--generation-observed-at cannot precede --submitted-at")
        if completed_value and submitted_value and completed_value < submitted_value:
            raise BridgeError("--response-completed-at cannot precede --submitted-at")
        if completed_value and generation_value and completed_value < generation_value:
            raise BridgeError("--response-completed-at cannot precede --generation-observed-at")
        response_wait_seconds = (
            int((completed_value - submitted_value).total_seconds())
            if completed_value and submitted_value
            else None
        )
        requested_model = args.requested_model.strip()
        selected_ui_label = args.selected_ui_label.strip()
        model_verification = assess_model_selection(
            requested_model, selected_ui_label, args.model_selection_kind
        )
        requested_thinking_intensity = args.requested_thinking_intensity.strip()
        selected_thinking_intensity = args.selected_thinking_intensity.strip()
        thinking_intensity_verification = (
            "verified"
            if requested_thinking_intensity
            and selected_thinking_intensity
            and requested_thinking_intensity == selected_thinking_intensity
            else "mismatch"
            if requested_thinking_intensity and selected_thinking_intensity
            else "unverified"
        )

        bridge_dir = bridge_root(repo)
        codex_meta = parse_metadata(
            bridge_dir / "codex-sessions" / codex_session_id / "session.md"
        )
        if not codex_meta:
            raise BridgeError(
                "Codex session metadata is required; run prepare_codex_session_notes.py first"
            )
        if codex_meta.get("bridge_thread_id") not in (None, "", thread_id):
            raise BridgeError(
                f"Codex session {codex_session_id} is bound to "
                f"{codex_meta['bridge_thread_id']}, not {thread_id}"
            )
        if args.standalone and args.bridge_project_id:
            raise BridgeError("--standalone cannot be combined with --bridge-project-id")
        capture_route = args.capture_route
        answer_format = args.answer_format or (
            "native-raw" if capture_route == "native-read-thread" else "plain-text-degraded"
        )
        if capture_route == "native-read-thread" and answer_format != "native-raw":
            raise BridgeError("Native read_thread capture requires --answer-format native-raw")
        if capture_route == "browser-fallback" and not args.answer_format:
            raise BridgeError(
                "Browser fallback requires explicit --answer-format copied-markdown "
                "or plain-text-degraded"
            )
        answer_sha256 = hashlib.sha256(answer.encode("utf-8")).hexdigest()
        remote_turn_id = args.remote_turn_id.strip()
        if remote_turn_id and not re.fullmatch(r"[A-Za-z0-9_-]{1,200}", remote_turn_id):
            raise BridgeError(
                "--remote-turn-id must contain only letters, digits, underscores, or hyphens"
            )
        if capture_route in {"native-read-thread", "browser-fallback"}:
            if not remote_turn_id:
                raise BridgeError(
                    f"--remote-turn-id is required for --capture-route {capture_route}"
                )
            if not args.expected_conversation_id.strip():
                raise BridgeError(
                    f"--expected-conversation-id is required for {capture_route} capture"
                )
        browser_identity_args = (
            args.observed_page_url,
            args.matching_page_count,
            args.observed_page_id,
            args.snapshot_page_id,
            args.observed_tab_owner_token,
        )
        if capture_route == "native-read-thread":
            if (
                args.browser_lease_token
                or any(browser_identity_args)
                or args.pages_json
                or args.owners_json
            ):
                raise BridgeError(
                    "Native read_thread capture must omit browser claim and tab observations"
                )
        elif capture_route == "browser-fallback":
            if not args.browser_lease_token or not all(browser_identity_args):
                raise BridgeError(
                    "Browser fallback requires claim token, page URL/id, fresh snapshot pageId, "
                    "and tab owner token"
                )
            owner_selected_count = None
            if bool(args.pages_json) != bool(args.owners_json):
                raise BridgeError(
                    "Browser fallback page and owner observations must be supplied together"
                )
            if args.pages_json:
                pages = parse_json_observation(args.pages_json, "--pages-json")
                owners = parse_json_observation(
                    args.owners_json, "--owners-json", owners=True
                )
                resolved = resolve_owned_tab(
                    repo,
                    claim_token=args.browser_lease_token,
                    thread_id=thread_id,
                    browser_profile=args.browser_profile,
                    expected_project_id=args.remote_project_id,
                    pages=pages,
                    owners=owners,
                )
                if resolved.get("action") != "reuse-owned-tab":
                    raise BridgeError(
                        f"Browser fallback tab resolution returned {resolved.get('action')!r} "
                        f"({resolved.get('reason', 'no-reason')}); HOLD"
                    )
                if resolved.get("matching_page_count") != args.matching_page_count:
                    raise BridgeError(
                        "Browser fallback matching count disagrees with the complete page list"
                    )
                if (
                    resolved.get("page_id") != normalize_page_id(args.observed_page_id)
                    or resolved.get("url") != args.observed_page_url.strip()
                ):
                    raise BridgeError(
                        "Browser fallback observations do not match the uniquely owned page"
                    )
                owner_selected_count = resolved.get("owner_selected_count")
            verify_claimed_tab(
                repo,
                claim_token=args.browser_lease_token,
                thread_id=thread_id,
                browser_profile=args.browser_profile,
                expected_project_id=args.remote_project_id,
                expected_conversation_id=args.expected_conversation_id,
                observed_page_url=args.observed_page_url,
                matching_page_count=args.matching_page_count,
                observed_page_id=args.observed_page_id,
                snapshot_page_id=args.snapshot_page_id,
                observed_tab_owner_token=args.observed_tab_owner_token,
                owner_selected_count=owner_selected_count,
            )
        elif (
            args.browser_lease_token
            or any(browser_identity_args)
            or args.pages_json
            or args.owners_json
        ):
            raise BridgeError(
                "Browser claim and tab observations are only accepted for browser-fallback capture"
            )
        if args.single_round:
            prior_exchange = any(
                event.get("event_type") == "gpt-exchange" and
                (not attempt_id or event.get("data", {}).get("attempt_id") != attempt_id)
                for event in load_events(bridge_dir, thread_id)
            )
            if prior_exchange:
                raise BridgeError(
                    f"--single-round: thread {thread_id} already has a captured "
                    "gpt-exchange; open a fresh probe thread for another round"
                )
        project_store = BridgeProjectStore(repo)
        bridge_project_id = (
            ""
            if args.standalone
            else args.bridge_project_id
            or codex_meta.get("bridge_project_id", "")
            or project_store.project_for_thread(thread_id)
        )
        remote_project_id = ""
        observed_workspace = args.observed_workspace.strip()
        observed_account_label = args.observed_account_label.strip()
        if args.standalone and project_store.project_for_thread(thread_id):
            raise BridgeError(
                f"--standalone requires an unattached thread, but {thread_id} is "
                "attached to a Bridge Project; open a fresh probe thread id"
            )
        if bridge_project_id:
            bridge_project_id = project_store.resolve_project_id(bridge_project_id)
            bridge_project = project_store.load_project(bridge_project_id)
            if bridge_project["status"] != "active":
                raise BridgeError(
                    f"Bridge Project {bridge_project_id} is archived; "
                    "reactivate it before capturing Project rounds"
                )
            if codex_meta.get("bridge_project_id") not in (
                None,
                "",
                bridge_project_id,
            ):
                raise BridgeError(
                    f"Codex session {codex_session_id} belongs to "
                    f"{codex_meta['bridge_project_id']}, not {bridge_project_id}"
                )
            if project_store.project_for_thread(thread_id) != bridge_project_id:
                raise BridgeError(
                    f"Bridge Thread {thread_id} is not attached to {bridge_project_id}"
                )
            binding = project_store.load_binding(bridge_project_id)
            if not binding or binding.get("status") != "active":
                raise BridgeError(
                    f"Bridge Project {bridge_project_id} has no active ChatGPT Project binding"
                )
            project_report = project_store.verify(bridge_project_id)
            if project_report["unsynced_source_count"]:
                raise BridgeError(
                    "Project Sources are not synchronized: "
                    f"{project_report['unsynced_source_count']} source record(s) "
                    "are pending, stale, failed, or missing"
                )
            remote_project_id = args.remote_project_id.strip()
            if not remote_project_id:
                raise BridgeError(
                    "Project-bound capture requires the visibly observed --remote-project-id"
                )
            if remote_project_id != binding.get("remote_project_id"):
                raise BridgeError(
                    f"Observed ChatGPT Project {remote_project_id} does not match "
                    f"the binding {binding.get('remote_project_id')}"
                )
            if not observed_workspace or not observed_account_label:
                raise BridgeError(
                    "Project-bound capture requires observed workspace and account labels"
                )
            if observed_workspace != binding.get("workspace"):
                raise BridgeError(
                    f"Observed workspace {observed_workspace!r} does not match "
                    f"the binding {binding.get('workspace')!r}"
                )
            if observed_account_label != binding.get("account_label"):
                raise BridgeError(
                    f"Observed account {observed_account_label!r} does not match "
                    f"the binding {binding.get('account_label')!r}"
                )
        elif (
            args.remote_project_id
            or observed_workspace
            or observed_account_label
        ):
            raise BridgeError(
                "Project observations require a Project-bound Bridge Thread"
            )
        sessions_dir = bridge_dir / "gpt-pro-sessions"
        session_dir = sessions_dir / gpt_session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        session_file = session_dir / "session.md"
        previous = parse_metadata(session_file)
        if previous.get("bridge_thread_id") not in (None, "", thread_id):
            raise BridgeError(
                f"GPT Pro session {gpt_session_id} is already bound to "
                f"{previous['bridge_thread_id']}; refusing to move it to {thread_id}"
            )
        if previous.get("codex_session_id") not in (None, "", codex_session_id):
            raise BridgeError(
                f"GPT Pro session {gpt_session_id} is already linked to Codex session "
                f"{previous['codex_session_id']}"
            )
        if previous.get("bridge_project_id") not in (
            None,
            "",
            bridge_project_id,
        ):
            raise BridgeError(
                f"GPT Pro session {gpt_session_id} belongs to "
                f"{previous['bridge_project_id']}, not {bridge_project_id or 'standalone'}"
            )
        if previous.get("remote_project_id") not in (
            None,
            "",
            remote_project_id,
        ):
            raise BridgeError(
                f"GPT Pro session {gpt_session_id} belongs to ChatGPT Project "
                f"{previous['remote_project_id']}, not {remote_project_id or 'standalone'}"
            )
        if previous.get("remote_project_workspace") not in (
            None,
            "",
            observed_workspace,
        ):
            raise BridgeError(
                f"GPT Pro session belongs to workspace "
                f"{previous['remote_project_workspace']!r}, not "
                f"{observed_workspace or 'standalone'!r}"
            )
        if previous.get("remote_account_label") not in (
            None,
            "",
            observed_account_label,
        ):
            raise BridgeError(
                f"GPT Pro session belongs to account "
                f"{previous['remote_account_label']!r}, not "
                f"{observed_account_label or 'standalone'!r}"
            )
        web_url = validate_web_url(args.web_url or previous.get("web_conversation_url", ""))
        if not web_url:
            raise BridgeError("--web-url is required when creating a GPT Pro session")
        if bridge_project_id:
            project_segment = project_segment_from_conversation_url(web_url)
            if not project_segment or (
                project_segment != remote_project_id
                and not project_segment.startswith(remote_project_id + "-")
            ):
                raise BridgeError(
                    "Project-bound conversation URL must belong to the bound "
                    f"ChatGPT Project {remote_project_id}"
                )
        if previous.get("web_conversation_url") not in (None, "", web_url):
            raise BridgeError("A GPT Pro session cannot be rebound to another web conversation URL")
        expected_conversation_id = args.expected_conversation_id.strip()
        if expected_conversation_id:
            observed_conversation_id = conversation_id_from_url(web_url)
            if observed_conversation_id != expected_conversation_id:
                raise BridgeError(
                    f"Bound conversation {observed_conversation_id or '<none>'!r} does not "
                    f"match the reserved conversation {expected_conversation_id!r}; refusing "
                    "to record this turn against the wrong chat"
                )

        notes_path = (
            resolve_repo_path(args.codex_notes, repo)
            if args.codex_notes
            else resolve_repo_path(codex_meta["latest_snapshot"], repo)
            if codex_meta.get("latest_snapshot")
            else bridge_dir / "codex-sessions" / codex_session_id / "notes.md"
        )
        if not notes_path.is_file():
            raise BridgeError("The immutable Codex notes snapshot is missing")
        codex_notes = repo_relative(notes_path, repo)
        bundle_path = ""
        bundle_sha256 = ""
        if args.bundle:
            bundle = resolve_repo_path(args.bundle, repo)
            if not bundle.is_file():
                raise BridgeError("--bundle must point to an existing file")
            bundle_path = repo_relative(bundle, repo)
            bundle_sha256 = file_sha256(bundle)
        attachment_name = args.attachment_name.strip()
        if attempt and bundle_path:
            preflight = attempt.get("preflight", {})
            if not isinstance(preflight, dict):
                raise BridgeError("Attempt preflight is invalid")
            staged = preflight.get("staged_bundle", {})
            if not isinstance(staged, dict):
                raise BridgeError("Attempt staged bundle provenance is invalid")
            if preflight.get("attachment_verification") != "verified":
                raise BridgeError(
                    "Attempt preflight does not contain a verified attachment"
                )
            canonical_name = str(
                preflight.get("attachment_name") or staged.get("attachment_name", "")
            ).strip()
            canonical_sha256 = str(
                preflight.get("attachment_sha256") or staged.get("staged_sha256", "")
            ).strip().lower()
            staged_name = str(staged.get("attachment_name", "")).strip()
            staged_sha256 = str(staged.get("staged_sha256", "")).strip().lower()
            source_sha256 = str(staged.get("source_sha256", "")).strip().lower()
            if (
                not canonical_name
                or not re.fullmatch(r"[0-9a-f]{64}", canonical_sha256)
                or not staged_name
                or not re.fullmatch(r"[0-9a-f]{64}", staged_sha256)
                or not re.fullmatch(r"[0-9a-f]{64}", source_sha256)
            ):
                raise BridgeError(
                    "Attempt preflight lacks the canonical attachment name and source/staged SHA-256"
                )
            if staged_name != canonical_name:
                raise BridgeError(
                    "Attempt preflight attachment name disagrees with staged verification"
                )
            if staged_sha256 != canonical_sha256:
                raise BridgeError(
                    "Attempt preflight attachment digest disagrees with staged verification"
                )
            if source_sha256 != canonical_sha256:
                raise BridgeError(
                    "Attempt preflight source digest disagrees with staged verification"
                )
            if attachment_name and attachment_name != canonical_name:
                raise BridgeError(
                    f"Captured attachment name {attachment_name!r} disagrees with the "
                    f"canonical staged name {canonical_name!r}"
                )
            if bundle_sha256 != canonical_sha256:
                raise BridgeError(
                    "Captured bundle digest disagrees with the canonical staged attachment digest"
                )
            attachment_name = canonical_name
            attachment_verification = "verified"
        elif attempt:
            preflight = attempt.get("preflight", {})
            if not isinstance(preflight, dict):
                raise BridgeError("Attempt preflight is invalid")
            if preflight.get("attachment_verification") == "verified":
                raise BridgeError(
                    "Attempt preflight contains an attachment, so --bundle is required "
                    "for digest verification"
                )
            if attachment_name:
                raise BridgeError("--attachment-name requires --bundle for attempt capture")
            attachment_verification = "not-required"
        else:
            attachment_verification = (
                "verified"
                if bundle_path and attachment_name == Path(bundle_path).name
                else "mismatch"
                if bundle_path and attachment_name
                else "not-required"
                if not bundle_path
                else "unverified"
            )

        title = one_line(args.turn_title or args.web_title or args.purpose or gpt_session_id, 120)
        capture_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "thread_id": thread_id,
                    "bridge_project_id": bridge_project_id,
                    "remote_project_id": remote_project_id,
                    "observed_workspace": observed_workspace,
                    "observed_account_label": observed_account_label,
                    "gpt_session_id": gpt_session_id,
                    "web_url": web_url,
                    "prompt": prompt,
                    "answer": answer,
                    "answer_format": answer_format,
                    "answer_sha256": answer_sha256,
                    "bundle_sha256": bundle_sha256,
                    "requested_model": requested_model,
                    "selected_ui_label": selected_ui_label,
                    "model_selection_kind": args.model_selection_kind,
                    "model_verification": model_verification,
                    "requested_thinking_intensity": requested_thinking_intensity,
                    "selected_thinking_intensity": selected_thinking_intensity,
                    "thinking_intensity_verification": thinking_intensity_verification,
                    "attachment_name": attachment_name,
                    "attachment_verification": attachment_verification,
                    "upload_control": args.upload_control.strip(),
                    # An inferred capture time is not stable across retries. Callers can
                    # pass an observed submission time to distinguish identical rounds.
                    "submitted_at": submitted_at if args.submitted_at.strip() else "",
                    "generation_observed_at": generation_observed_at,
                    "response_completed_at": response_completed_at,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        if attempt:
            # Retries after saving an exchange but before checkpoint completion reuse the same artifact.
            capture_fingerprint = hashlib.sha256(json.dumps(
                {"attempt_id": attempt_id, "thread_id": thread_id,
                 "remote_turn_id": remote_turn_id, "prompt": prompt, "answer": answer,
                 "answer_format": answer_format},
                ensure_ascii=False, sort_keys=True,
            ).encode("utf-8")).hexdigest()
            reserve_capture(repo, thread_id, attempt_id, capture_fingerprint)
        with file_lock(session_dir / ".session.lock"):
            turn_file = next(
                (
                    path
                    for path in sorted(session_dir.glob("*.md"))
                    if f"- Capture Fingerprint: {capture_fingerprint}" in path.read_text(
                        encoding="utf-8"
                    )
                ),
                None,
            )
            if turn_file is None:
                number = next_turn_number(session_dir)
                turn_file = session_dir / f"{number:03d}-{slugify(title)}.md"
                if turn_file.exists():
                    raise BridgeError(f"Refusing to overwrite turn: {turn_file}")
                atomic_write_text(
                    turn_file,
                    build_turn(
                        number=number,
                        title=title,
                        thread_id=thread_id,
                        bridge_project_id=bridge_project_id,
                        remote_project_id=remote_project_id,
                        observed_workspace=observed_workspace,
                        observed_account_label=observed_account_label,
                        gpt_session_id=gpt_session_id,
                        codex_session_id=codex_session_id,
                        web_url=web_url,
                        web_title=args.web_title or previous.get("web_title", ""),
                        codex_notes=codex_notes,
                        bundle_path=bundle_path,
                        bundle_sha256=bundle_sha256,
                        submitted_at=submitted_at,
                        generation_observed_at=generation_observed_at,
                        response_completed_at=response_completed_at,
                        response_wait_seconds=response_wait_seconds,
                        requested_model=requested_model,
                        selected_ui_label=selected_ui_label,
                        model_selection_kind=args.model_selection_kind,
                        model_verification=model_verification,
                        requested_thinking_intensity=requested_thinking_intensity,
                        selected_thinking_intensity=selected_thinking_intensity,
                        thinking_intensity_verification=thinking_intensity_verification,
                        attachment_name=attachment_name,
                        attachment_verification=attachment_verification,
                        upload_control=args.upload_control.strip(),
                        capture_route=capture_route,
                        answer_format=answer_format,
                        answer_sha256=answer_sha256,
                        remote_turn_id=remote_turn_id,
                        saved_at=saved_at,
                        prompt=prompt,
                        answer=answer,
                        capture_summary=summary,
                        capture_fingerprint=capture_fingerprint,
                    ),
                )
            else:
                match = re.match(r"^(\d+)-", turn_file.name)
                if not match:
                    raise BridgeError(f"Cannot recover turn number from {turn_file}")
                number = int(match.group(1))
            write_bound_metadata(
                session_file,
                {
                    "gpt_pro_session_id": gpt_session_id,
                    "bridge_thread_id": thread_id,
                    "bridge_project_id": bridge_project_id,
                    "remote_project_id": remote_project_id,
                    "remote_project_workspace": observed_workspace,
                    "remote_account_label": observed_account_label,
                    "codex_session_id": codex_session_id,
                    "web_conversation_url": web_url,
                    "web_title": args.web_title or previous.get("web_title", "") or title,
                    "purpose": args.purpose or previous.get("purpose", "") or title,
                    "created_at": previous.get("created_at", "") or saved_at,
                    "last_used_at": saved_at,
                    "latest_turn": f"{number:03d}",
                },
                ordered_keys=(
                    "gpt_pro_session_id",
                    "bridge_thread_id",
                    "bridge_project_id",
                    "remote_project_id",
                    "remote_project_workspace",
                    "remote_account_label",
                    "codex_session_id",
                    "web_conversation_url",
                    "web_title",
                    "purpose",
                    "created_at",
                    "last_used_at",
                    "latest_turn",
                ),
                immutable_keys=(
                    "gpt_pro_session_id",
                    "bridge_thread_id",
                    "bridge_project_id",
                    "remote_project_id",
                    "remote_project_workspace",
                    "remote_account_label",
                    "codex_session_id",
                    "web_conversation_url",
                    "created_at",
                ),
            )
        write_session_index(sessions_dir, kind="gpt-pro")
        turn_rel = repo_relative(turn_file, repo)
        append_event(
            repo,
            thread_id=thread_id,
            event_type="gpt-exchange",
            actor="gpt-pro",
            thread_title=args.purpose or args.web_title or title,
            bridge_project_id=bridge_project_id,
            codex_session_id=codex_session_id,
            gpt_pro_session_id=gpt_session_id,
            artifact={"kind": "gpt-pro-turn", "path": turn_rel, "sha256": file_sha256(turn_file)},
            data={
                "turn": turn_rel,
                "question": one_line(prompt),
                "summary": one_line(summary),
                "bundle": bundle_path,
                "bundle_sha256": bundle_sha256,
                "web_title": args.web_title or previous.get("web_title", "") or title,
                "remote_project_id": remote_project_id,
                "observed_workspace": observed_workspace,
                "observed_account_label": observed_account_label,
                "requested_model": requested_model,
                "selected_ui_label": selected_ui_label,
                "model_selection_kind": args.model_selection_kind,
                "model_verification": model_verification,
                "requested_thinking_intensity": requested_thinking_intensity,
                "selected_thinking_intensity": selected_thinking_intensity,
                "thinking_intensity_verification": thinking_intensity_verification,
                "attachment_name": attachment_name,
                "attachment_verification": attachment_verification,
                "upload_control": args.upload_control.strip(),
                "capture_route": capture_route,
                "answer_format": answer_format,
                "answer_sha256": answer_sha256,
                "remote_turn_id": remote_turn_id,
                "attempt_id": attempt_id,
                "prompt_sha256": hashlib.sha256(prompt.encode("utf-8")).hexdigest(),
                "submitted_at": submitted_at,
                "generation_observed_at": generation_observed_at,
                "response_completed_at": response_completed_at,
                "response_wait_seconds": response_wait_seconds,
                "observed_conversation_id": conversation_id_from_url(web_url),
            },
            dedupe_key=f"gpt-exchange:{capture_fingerprint}",
            occurred_at=saved_at,
        )

        if attempt:
            complete_attempt(repo, thread_id, attempt_id, turn_file)

        if verification or decision_trail:
            verdict_path = record_codex_verdict(
                repo,
                thread_id=thread_id,
                gpt_pro_session_id=gpt_session_id,
                codex_session_id=codex_session_id,
                bridge_project_id=bridge_project_id,
                turn_path=turn_file,
                summary=summary,
                verification=verification,
                decision_trail=decision_trail,
            )
            print(f"Immediate verdict: {verdict_path}", file=sys.stderr)
        print(turn_file)
        return 0
    except (BridgeError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
