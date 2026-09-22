"""Durable submission checkpoints; browser and scheduler calls stay with the host."""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import uuid
from pathlib import Path

from bridge_store import (
    BridgeError, atomic_write_text, bridge_root, file_lock, file_sha256,
    load_events, now_iso, repo_relative, resolve_repo_path, validate_id,
)
from browser_identity import conversation_identity_from_url, normalize_page_id, project_url_matches


TERMINAL = {"captured", "failed"}


def timestamp(value: str) -> dt.datetime:
    try:
        parsed = dt.datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("timezone missing")
        return parsed
    except (TypeError, ValueError) as exc:
        raise BridgeError("A timezone-aware ISO-8601 timestamp is required") from exc


def optional_timestamp(value: str | None) -> dt.datetime | None:
    """Parse an explicitly supplied deadline; None/empty means no business cutoff."""
    if value is None or value == "":
        return None
    return timestamp(value)


def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _directory(repo: Path, thread_id: str) -> Path:
    return resolve_repo_path(
        str(bridge_root(repo) / "attempts" / validate_id(thread_id, "bridge thread id")),
        repo, must_exist=False,
    )


def _read(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BridgeError(f"Cannot read attempt checkpoint: {path}") from exc
    if not isinstance(value, dict) or value.get("schema_version") != 1:
        raise BridgeError("Unsupported attempt checkpoint")
    if value.get("attempt_id") != path.stem or value.get("thread_id") != path.parent.name:
        raise BridgeError("Attempt checkpoint identity does not match its path")
    if value.get("prompt_sha256") != digest(value.get("prompt", "")):
        raise BridgeError("Attempt prompt digest mismatch")
    return value


def read_attempt(repo: Path, thread_id: str, attempt_id: str) -> dict:
    path = _directory(repo, thread_id) / f"{validate_id(attempt_id, 'attempt id')}.json"
    return _read(path)


def _save(path: Path, value: dict) -> None:
    value["updated_at"] = now_iso()
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, sort_keys=True) + "\n")


def active_attempt(repo: Path, thread_id: str) -> dict | None:
    directory = _directory(repo, thread_id)
    if not directory.exists():
        return None
    with file_lock(directory / ".lock"):
        pending = [a for p in directory.glob("*.json")
                   if (a := _read(p))["state"] not in TERMINAL or
                   (a["state"] == "failed" and a.get("send_started_at"))]
    if len(pending) > 1:
        raise BridgeError("Multiple unfinished attempts; HOLD")
    return pending[0] if pending else None


def assert_preflight_allowed(repo: Path, thread_id: str, prompt_sha256: str,
                             boundary: str, owner: str) -> None:
    attempt = active_attempt(repo, thread_id)
    if attempt and (attempt["state"] != "prepared" or
                    (attempt["prompt_sha256"], attempt["pre_submit_boundary"], attempt["tab_owner_token"]) !=
                    (prompt_sha256, boundary, owner)):
        raise BridgeError("Unresolved attempt: inspect its exact turn; another Send is not authorized")


def prepare_attempt(repo: Path, thread_id: str, prompt: str, preflight: dict,
                    deadline: str | None = None) -> dict:
    deadline_value = deadline or None
    parsed_deadline = optional_timestamp(deadline_value)
    if parsed_deadline is not None and parsed_deadline <= dt.datetime.now().astimezone():
        raise BridgeError("Attempt deadline must be in the future")
    if not prompt.strip() or preflight.get("ready") is not True:
        raise BridgeError("A non-empty prompt and successful preflight are required")
    for field in ("browser_claim_verification", "turn_boundary_verification"):
        if preflight.get(field) != "verified":
            raise BridgeError(f"Preflight lacks {field}")
    if preflight.get("bridge_thread_id") != thread_id:
        raise BridgeError("Preflight belongs to another Bridge Thread")
    if preflight.get("prompt_sha256") != digest(prompt):
        raise BridgeError("Prompt does not match preflight SHA-256")
    if not preflight.get("pre_submit_boundary") or not preflight.get("tab_owner_token"):
        raise BridgeError("Preflight boundary and owner are required")
    directory = _directory(repo, thread_id)
    with file_lock(directory / ".lock"):
        for path in directory.glob("*.json"):
            old = _read(path)
            if (old["state"] not in TERMINAL or
                    (old["state"] == "failed" and old.get("send_started_at")) or
                    old.get("watcher", {}).get("state") in {"registering", "active"}):
                raise BridgeError(f"Existing attempt {old['attempt_id']} must be recovered before another round")
        attempt_id = uuid.uuid4().hex
        attempt = {
            "schema_version": 1, "thread_id": thread_id, "attempt_id": attempt_id,
            "state": "prepared", "created_at": now_iso(), "deadline": deadline_value,
            "prompt": prompt, "prompt_sha256": digest(prompt), "preflight": preflight,
            "pre_submit_boundary": preflight["pre_submit_boundary"],
            "tab_owner_token": preflight["tab_owner_token"],
            "project_id": preflight.get("expected_project_id", ""),
            "conversation_id": preflight.get("expected_conversation_id", ""),
            "conversation_url": "" if preflight.get("conversation_bootstrap") else preflight["observed_page_url"],
            "remote_turn_id": "", "submitted_at": "", "watcher": {},
        }
        _save(directory / f"{attempt_id}.json", attempt)
    return attempt


def _mutate(repo: Path, thread_id: str, attempt_id: str, operation) -> dict:
    directory = _directory(repo, thread_id)
    with file_lock(directory / ".lock"):
        path = directory / f"{validate_id(attempt_id, 'attempt id')}.json"
        attempt = _read(path)
        operation(attempt)
        _save(path, attempt)
    return attempt


def mark_send_started(repo: Path, thread_id: str, attempt_id: str) -> dict:
    def change(a):
        if a["state"] != "prepared":
            raise BridgeError("Send was already attempted; do not click Send again")
        deadline = optional_timestamp(a.get("deadline"))
        if deadline is not None and deadline <= dt.datetime.now().astimezone():
            raise BridgeError("Attempt deadline expired before Send")
        a.update(state="send-started", send_started_at=now_iso())
    return _mutate(repo, thread_id, attempt_id, change)


def record_submission(repo: Path, thread_id: str, attempt_id: str, *,
                      conversation_url: str, owner: str, prompt_sha256: str,
                      boundary: str, remote_turn_id: str, after_boundary: str,
                      submitted_at: str = "") -> dict:
    def change(a):
        if a["state"] not in {"send-started", "submitted", "failed"} or not a.get("send_started_at"):
            raise BridgeError("Submission observation requires a started attempt")
        if (owner, prompt_sha256, boundary) != (a["tab_owner_token"], a["prompt_sha256"], a["pre_submit_boundary"]):
            raise BridgeError("Submission observation does not match checkpoint identity")
        project_id, conversation_id = conversation_identity_from_url(conversation_url)
        if not project_url_matches(project_id, a["project_id"]):
            raise BridgeError("Observed conversation is in another Project")
        if a["conversation_id"] and a["conversation_id"] != conversation_id:
            raise BridgeError("Attempt cannot move to another conversation")
        if not remote_turn_id or after_boundary != "yes":
            raise BridgeError("Exact submitted user turn after the saved boundary is required")
        if a["remote_turn_id"] and a["remote_turn_id"] != remote_turn_id:
            raise BridgeError("Attempt cannot move to another remote turn")
        if submitted_at:
            if timestamp(submitted_at) < timestamp(a["send_started_at"]):
                raise BridgeError("Submission time precedes Send checkpoint")
            if a["submitted_at"] and a["submitted_at"] != submitted_at:
                raise BridgeError("Observed submission time cannot change")
            a["submitted_at"] = submitted_at
        a.update(state="submitted", conversation_id=conversation_id, conversation_url=conversation_url,
                 remote_turn_id=remote_turn_id)
        a.setdefault("submission_observed_at", now_iso())
    return _mutate(repo, thread_id, attempt_id, change)


def recovery_guard(repo: Path, thread_id: str, attempt_id: str, *, prompt_sha256: str,
                   boundary: str, owner: str, composer_state: str) -> dict | None:
    a = read_attempt(repo, thread_id, attempt_id) if attempt_id else active_attempt(repo, thread_id)
    if a is None:
        return None
    if (a["prompt_sha256"], a["pre_submit_boundary"], a["tab_owner_token"]) != (prompt_sha256, boundary, owner):
        raise BridgeError("Recovery observations do not match durable attempt")
    if a["state"] == "captured" or (a["state"] == "failed" and not a.get("send_started_at")):
        raise BridgeError("Attempt is terminal; do not resume sending")
    if a["state"] != "prepared" and composer_state in {"empty", "expected-attachment"}:
        raise BridgeError("Send may have happened; an empty composer never authorizes resubmission")
    return a


def runtime_capabilities(inventory: dict) -> dict:
    tools = inventory.get("available_tools")
    if not isinstance(tools, list) or any(not isinstance(name, str) for name in tools):
        raise BridgeError("Capabilities require available_tools from the current runtime")
    roles = {}
    for role in ("chatgpt_read_tool", "browser_read_tool", "browser_wait_tool",
                 "watcher_create_tool", "watcher_stop_tool"):
        name = inventory.get(role, "")
        if name and name not in tools:
            raise BridgeError(f"{role} is not present in the current tool inventory")
        roles[role] = name
    return roles


def wait_plan(a: dict, inventory: dict) -> dict:
    roles = runtime_capabilities(inventory)
    watcher = a.get("watcher", {})
    deadline = optional_timestamp(a.get("deadline"))
    expired = deadline is not None and deadline <= dt.datetime.now().astimezone()
    terminal = a["state"] in TERMINAL
    result = {"attempt_id": a["attempt_id"], "state": a["state"], "deadline": a.get("deadline"),
              "conversation_url": a["conversation_url"], "remote_turn_id": a["remote_turn_id"],
              "may_resend": False, "background_registered": watcher.get("state") == "active"}
    if watcher.get("state") == "registering":
        return {**result, "action": "reconcile-watcher-registration", "mode": "hold"}
    if terminal or expired:
        return {**result, "action": "stop-watcher" if watcher.get("state") == "active" else
                "terminal" if terminal else "record-timeout", "mode": "terminal",
                "automation_id": watcher.get("automation_id", "")}
    if a["state"] == "prepared":
        return {**result, "action": "complete-preflight-before-send", "mode": "active-session"}
    read_tool = roles["chatgpt_read_tool"] or roles["browser_read_tool"] or roles["browser_wait_tool"]
    if not read_tool:
        return {**result, "action": "needs-reader", "mode": "manual"}
    if not a["conversation_url"]:
        return {**result, "action": "recover-owned-tab-and-promote", "mode": "active-session"}
    if watcher.get("state") == "active":
        return {**result, "action": "await-registered-watcher", "mode": "scheduled",
                "automation_id": watcher["automation_id"]}
    if roles["browser_wait_tool"] and a["remote_turn_id"]:
        return {**result, "action": "wait-exact-turn", "mode": "active-session",
                "read_tool": roles["browser_wait_tool"], "script_command": "wait-script",
                "requires_live_session": True, "can_register_watcher": bool(
                    roles["watcher_create_tool"] and roles["watcher_stop_tool"])}
    return {**result, "action": "read-exact-turn", "mode": "active-session", "read_tool": read_tool,
            "next_check_after_seconds": 30, "requires_live_session": True,
            "can_register_watcher": bool(roles["watcher_create_tool"] and roles["watcher_stop_tool"])}


def browser_wait_script(a: dict, observed_page_url: str = "") -> dict:
    """Build a passive DOM reader from the already verified submitted identity."""
    if a["state"] != "submitted" or not all(a.get(k) for k in (
            "conversation_url", "tab_owner_token", "remote_turn_id")):
        raise BridgeError("Browser wait requires the pinned submitted turn")
    config = {"attempt_id": a["attempt_id"], "conversation_url": a["conversation_url"],
              "tab_owner_token": a["tab_owner_token"], "remote_turn_id": a["remote_turn_id"],
              "deadline": a.get("deadline")}
    if observed_page_url:
        project, conversation = conversation_identity_from_url(observed_page_url)
        if conversation != a["conversation_id"] or not project_url_matches(project, a["project_id"]):
            raise BridgeError("Resolved page does not identify the submitted conversation")
        config["conversation_url"] = observed_page_url
    optional_timestamp(config["deadline"])
    source = (Path(__file__).resolve().parent.parent / "gpt-pro-question-window" /
              "scripts/wait_for_reply.js").read_text(encoding="utf-8")
    return {"function": f"async () => {{\n{source}\nreturn await waitForReply({json.dumps(config)});\n}}"}


def browser_wait_exec(a: dict, page_id: str, observed_page_url: str = "") -> str:
    """Compose bounded browser calls without returning empty batches to the model."""
    request = {**browser_wait_script(a, observed_page_url),
               "pageId": int(normalize_page_id(page_id)), "waitForStableDom": False}
    return "const request = " + json.dumps(request) + ";\n" + r'''
while (true) {
  const reply = await tools.mcp__chrome_devtools__evaluate_script(request);
  if (reply.isError) throw new Error(JSON.stringify(reply));
  const blocks = (reply.content || []).filter(item => item.type === 'text');
  const match = blocks.map(item => item.text).join('\n').match(/```json\s*([\s\S]*?)\s*```/);
  if (!match) throw new Error('Unexpected browser wait result: ' + JSON.stringify(reply));
  const event = JSON.parse(match[1]);
  if (event.status === 'pending') continue;
  if (!['ready-for-capture', 'identity-changed', 'target-not-visible',
        'ambiguous-answer', 'deadline'].includes(event.status)) {
    throw new Error('Unexpected browser wait status: ' + JSON.stringify(event));
  }
  text(event);
  break;
}
'''


def reserve_watcher(repo: Path, thread_id: str, attempt_id: str, inventory: dict) -> dict:
    roles = runtime_capabilities(inventory)
    def change(a):
        deadline = optional_timestamp(a.get("deadline"))
        if a["state"] != "submitted" or (
                deadline is not None and deadline <= dt.datetime.now().astimezone()):
            raise BridgeError("Watcher requires a submitted, unexpired attempt")
        if not (roles["chatgpt_read_tool"] or roles["browser_read_tool"] or roles["browser_wait_tool"]) or not all(
                roles[k] for k in ("watcher_create_tool", "watcher_stop_tool")):
            raise BridgeError("Current host lacks a reader or task wake-up/stop tools")
        if a["watcher"]:
            raise BridgeError("Watcher already reserved; reconcile it instead of creating another")
        a["watcher"] = {"state": "registering", "tools": roles, "reserved_at": now_iso()}
    return _mutate(repo, thread_id, attempt_id, change)


def register_watcher(repo: Path, thread_id: str, attempt_id: str, automation_id: str) -> dict:
    def change(a):
        watcher = a["watcher"]
        if watcher.get("state") == "active" and watcher.get("automation_id") == automation_id:
            return
        if watcher.get("state") != "registering" or not automation_id.strip():
            raise BridgeError("Register only the real automation ID returned for the reserved watcher")
        watcher.update(state="active", automation_id=automation_id.strip())
    return _mutate(repo, thread_id, attempt_id, change)


def stop_watcher_record(repo: Path, thread_id: str, attempt_id: str, automation_id: str) -> dict:
    def change(a):
        if a["watcher"].get("automation_id") != automation_id or not automation_id:
            raise BridgeError("Watcher stop receipt does not identify the registered automation")
        a["watcher"].update(state="stopped", stopped_at=now_iso())
    return _mutate(repo, thread_id, attempt_id, change)


def fail_attempt(repo: Path, thread_id: str, attempt_id: str, reason: str) -> dict:
    def change(a):
        if a["state"] == "captured" or not reason.strip():
            raise BridgeError("Failure requires a reason and an uncaptured attempt")
        a.update(state="failed", failure=reason.strip())
    return _mutate(repo, thread_id, attempt_id, change)


def validate_capture(repo: Path, thread_id: str, attempt_id: str, *, prompt: str,
                     conversation_url: str, remote_turn_id: str) -> dict:
    a = read_attempt(repo, thread_id, attempt_id)
    project_id, conversation_id = conversation_identity_from_url(conversation_url)
    if (a["state"] not in {"submitted", "captured"} or a["prompt_sha256"] != digest(prompt)
            or a["conversation_id"] != conversation_id or not project_url_matches(project_id, a["project_id"])
            or not remote_turn_id or a["remote_turn_id"] != remote_turn_id):
        raise BridgeError("Capture does not match the pinned attempt and remote turn")
    return a


def complete_attempt(repo: Path, thread_id: str, attempt_id: str, turn: Path) -> dict:
    artifact = {"path": repo_relative(turn, repo), "sha256": file_sha256(turn)}
    attempt = read_attempt(repo, thread_id, attempt_id)
    if not any(event.get("event_type") == "gpt-exchange" and
               event.get("artifact", {}).get("path") == artifact["path"] and
               event.get("artifact", {}).get("sha256") == artifact["sha256"] and
               event.get("data", {}).get("remote_turn_id") == attempt["remote_turn_id"] and
               event.get("data", {}).get("observed_conversation_id") == attempt["conversation_id"] and
               event.get("data", {}).get("attempt_id") == attempt_id and
               event.get("data", {}).get("prompt_sha256") == attempt["prompt_sha256"]
               for event in load_events(bridge_root(repo), thread_id)):
        raise BridgeError("Capture must match the pinned attempt in the canonical exchange ledger")
    def change(a):
        if a["state"] not in {"submitted", "captured"}:
            raise BridgeError("Only a submitted attempt can complete")
        if a.get("capture") and a["capture"] != artifact:
            raise BridgeError("Attempt already captured a different artifact")
        a.update(state="captured", capture=artifact)
    return _mutate(repo, thread_id, attempt_id, change)


def reserve_capture(repo: Path, thread_id: str, attempt_id: str, fingerprint: str) -> dict:
    """Pin one answer before writing its artifact/ledger; retries cannot change the answer."""
    def change(a):
        if a["state"] not in {"submitted", "captured"}:
            raise BridgeError("Capture reservation requires a submitted attempt")
        if a.get("capture_fingerprint") and a["capture_fingerprint"] != fingerprint:
            raise BridgeError("Attempt already reserved a different answer; HOLD before writing")
        a["capture_fingerprint"] = fingerprint
    return _mutate(repo, thread_id, attempt_id, change)
