#!/usr/bin/env python3
"""Freeze one deterministic Bridge Executor handoff.

This command owns only parent-side input preparation: Project routing/rebind,
route-derived Thread allocation, and atomic request/handoff publication.  The
Executor remains the sole owner of bundle creation, staging, browser mutation,
waiting, capture, and cleanup.
"""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from typing import Any

SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import (
    BridgeError,
    atomic_write_text,
    bridge_root,
    default_gpt_session_id,
    file_lock,
    file_sha256,
    now_iso,
    parse_metadata,
    validate_id,
)
from executor_handoff import CONTEXT_POLICIES, HANDOFF_V2, validate_handoff
from model_controls import MODEL_SELECTION_KINDS
from project_router import resolve_route
from project_store import (
    BridgeProjectStore,
    normalize_remote_project_url,
    remote_project_id_from_url,
)


def _slug(value: str) -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug[:64].strip("-") or "bridge-project"


def _read_text(path_value: str, *, label: str) -> str:
    path = Path(path_value).expanduser().resolve()
    if not path.is_file():
        raise BridgeError(f"{label} must be an existing file: {path}")
    text = path.read_text(encoding="utf-8")
    if not text.strip():
        raise BridgeError(f"{label} must not be empty: {path}")
    return text


def _repo_file(repo: Path, value: str, *, label: str) -> str:
    path = Path(value).expanduser()
    path = path.resolve() if path.is_absolute() else (repo / path).resolve()
    if not path.is_file() or not path.is_relative_to(repo):
        raise BridgeError(f"{label} must be a file inside repo: {value}")
    return path.relative_to(repo).as_posix()


def _sha256_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _publish_transaction(output_dir: Path, artifacts: dict[str, str]) -> None:
    """Publish the preparation set as one directory-level transaction."""
    parent = output_dir.parent
    parent.mkdir(parents=True, exist_ok=True)
    with file_lock(parent / ".lock"):
        if output_dir.is_symlink():
            raise BridgeError(f"Preparation output path must not be a symlink: {output_dir}")
        if output_dir.exists():
            if not output_dir.is_dir():
                raise BridgeError(f"Preparation output path is not a safe directory: {output_dir}")
            for name, text in artifacts.items():
                path = output_dir / name
                if not path.is_file() or path.read_text(encoding="utf-8") != text:
                    raise BridgeError(f"Existing preparation artifact conflicts: {path}")
            return
        temporary = Path(tempfile.mkdtemp(prefix=f".{output_dir.name}-", dir=str(parent)))
        try:
            for name, text in artifacts.items():
                atomic_write_text(temporary / name, text)
            os.replace(temporary, output_dir)
        except Exception:
            shutil.rmtree(temporary, ignore_errors=True)
            raise


def _local_project_id(store: BridgeProjectStore, explicit: str, *, create: bool) -> str:
    if explicit:
        return store.resolve_project_id(explicit)
    project_ids = store.list_project_ids()
    if len(project_ids) > 1:
        raise BridgeError("Multiple Bridge Projects are visible; specify --bridge-project-id")
    if project_ids:
        return project_ids[0]
    if not create:
        return ""
    project_id = validate_id(_slug(store.repo.name), "bridge project id")
    store.create_project(project_id, title=store.repo.name)
    return project_id


def _target_project(
    store: BridgeProjectStore,
    *,
    bridge_project_id: str,
    target_url: str,
    remote_project_id: str,
) -> tuple[str, str, str, str, bool]:
    """Apply an explicit local rebind and return target identity/action."""
    normalized = normalize_remote_project_url(target_url)
    if remote_project_id:
        try:
            url_project_id = remote_project_id_from_url(normalized)
        except BridgeError:
            url_project_id = ""
        if url_project_id and url_project_id != remote_project_id:
            raise BridgeError(
                f"Target Project URL identifies {url_project_id}, not the supplied "
                f"--remote-project-id {remote_project_id}"
            )
    remote_id = remote_project_id_from_url(normalized, remote_project_id)
    project_id = _local_project_id(store, bridge_project_id, create=True)
    previous = store.load_binding(project_id)
    previous_id = str(previous.get("remote_project_id", ""))
    target_changed = previous_id != remote_id
    action = (
        "verify"
        if previous_id == remote_id and previous.get("status") == "active"
        else "rebind-and-verify"
    )
    store.bind_remote(
        project_id,
        remote_url=normalized,
        remote_project_id=remote_id,
        verified=False,
        allow_rebind=True,
    )
    binding = store.load_binding(project_id)
    return (
        project_id,
        remote_id,
        str(binding.get("remote_project_url", normalized)),
        action,
        target_changed,
    )


def _binding_action(
    store: BridgeProjectStore,
    project_id: str,
    *,
    explicit_target: bool,
) -> str:
    if explicit_target:
        return "verify"
    if not project_id:
        return "none"
    binding = store.load_binding(project_id)
    return "reuse" if binding.get("status") == "active" else "verify"


def _context_policy(args: argparse.Namespace, files: list[str]) -> tuple[str, int]:
    """Validate the frozen repository-context choice before any Project rebind."""
    policy = str(getattr(args, "context_policy", "explicit") or "").strip()
    if policy not in CONTEXT_POLICIES:
        raise BridgeError(
            "context policy must be one of: " + ", ".join(CONTEXT_POLICIES)
        )
    configured_max = getattr(args, "max_files", 24)
    if isinstance(configured_max, bool) or not isinstance(configured_max, int):
        raise BridgeError("max-files must be an integer")
    if configured_max < 0:
        raise BridgeError("max-files must not be negative")
    if policy == "none":
        if files:
            raise BridgeError("context policy none cannot be combined with --file")
        return policy, 0
    if policy == "explicit":
        if not files:
            raise BridgeError("context policy explicit requires at least one --file")
        # Explicit evidence is a closed list, so freeze its exact cardinality.
        return policy, len(files)
    if configured_max <= 0:
        raise BridgeError("max-files must be positive for auto context policy")
    if len(files) > configured_max:
        raise BridgeError(
            "auto context max-files cannot be smaller than the explicit file count"
        )
    return policy, configured_max


def prepare(args: argparse.Namespace) -> dict[str, Any]:
    repo = Path(args.repo).expanduser().resolve()
    if not repo.is_dir():
        raise BridgeError(f"Repository root is not a directory: {repo}")
    if args.standalone and args.target_project_url:
        raise BridgeError("--standalone cannot be combined with --target-project-url")
    if args.standalone and args.bridge_project_id:
        raise BridgeError("--standalone cannot be combined with --bridge-project-id")
    if args.remote_project_id and not args.target_project_url:
        raise BridgeError("--remote-project-id requires --target-project-url")
    goal = args.goal.strip()
    task = (args.task or args.goal).strip()
    if not goal or not task:
        raise BridgeError("goal and task must be non-empty")
    if not args.allow_send:
        raise BridgeError("prepare-and-run requires explicit --allow-send")
    requested_model = args.requested_model.strip()
    requested_intensity = args.requested_thinking_intensity.strip()
    selection_kind = args.model_selection_kind.strip()
    if not requested_model or not requested_intensity:
        raise BridgeError("requested model and thinking intensity are required")
    if selection_kind not in MODEL_SELECTION_KINDS:
        raise BridgeError("model_selection_kind is invalid")
    # Check the policy/list combination before touching any Project store.  The
    # resolved paths are validated below, but a contradictory policy is itself
    # an input error and must not be masked by path resolution.
    raw_files = list(args.files or [])
    context_policy, max_files = _context_policy(args, raw_files)
    question = _read_text(args.question_file, label="question file")
    notes = _repo_file(repo, args.notes, label="notes")
    files = [_repo_file(repo, value, label="evidence file") for value in raw_files]
    if context_policy == "explicit":
        max_files = len(files)
    if args.business_deadline:
        try:
            parsed_deadline = dt.datetime.fromisoformat(args.business_deadline)
        except ValueError as exc:
            raise BridgeError("business deadline must be ISO-8601") from exc
        if parsed_deadline.tzinfo is None:
            raise BridgeError("business deadline must include a timezone")

    store = BridgeProjectStore(repo)
    project_id = ""
    remote_id = ""
    remote_url = ""
    binding_action = "none"
    target_changed = False
    explicit_target = bool(args.target_project_url)
    if explicit_target:
        project_id, remote_id, remote_url, binding_action, target_changed = _target_project(
            store,
            bridge_project_id=args.bridge_project_id,
            target_url=args.target_project_url,
            remote_project_id=args.remote_project_id,
        )
        requested_scope = "project"
    elif args.standalone:
        requested_scope = "standalone"
    else:
        project_id = _local_project_id(store, args.bridge_project_id, create=False)
        requested_scope = "auto"

    route = resolve_route(
        repo,
        task=task,
        requested_scope=requested_scope,
        requires_external_reasoning=True,
        bridge_project_id=project_id,
        force_new_thread=explicit_target and target_changed,
        attach=False,
    )
    if route.requires_confirmation:
        allowed = {"verify-project-source-inventory"}
        if explicit_target:
            allowed.add("verify-or-bind-chatgpt-project")
        unexpected = [item for item in route.requires_confirmation if item not in allowed]
        if unexpected:
            raise BridgeError(
                "Bridge route is not ready: " + ", ".join(route.requires_confirmation)
            )
    if route.scope == "project":
        project_id = route.bridge_project_id
        remote_id = remote_id or route.remote_project_id
        remote_url = remote_url or route.remote_project_url
        if not project_id or not remote_id or not remote_url:
            raise BridgeError("Project route did not resolve a complete Project identity")
        if not explicit_target:
            binding_action = _binding_action(store, project_id, explicit_target=False)
        store.attach_thread(project_id, route.bridge_thread_id, title=task, goal=goal)
    else:
        project_id = ""
        remote_id = ""
        remote_url = ""
        binding_action = "none"

    thread_id = route.bridge_thread_id
    output_dir = bridge_root(repo) / "executor-preparations" / thread_id
    request_path = output_dir / "request.json"
    handoff_path = output_dir / "executor_handoff.json"
    receipt_path = output_dir / "receipt.json"
    gpt_session_id = default_gpt_session_id(thread_id)
    session_meta = parse_metadata(
        bridge_root(repo) / "gpt-pro-sessions" / gpt_session_id / "session.md"
    )
    conversation_url = str(session_meta.get("web_conversation_url", ""))
    request = {
        "repo": str(repo),
        "bridge_thread_id": thread_id,
        "goal": goal,
        "question": question,
        "notes": notes,
        "files": files,
        "file_digests": {
            path: file_sha256(repo / path)
            for path in dict.fromkeys([notes, *files])
        },
        "mode": args.mode,
        "context_policy": context_policy,
        "max_files": max_files,
    }
    if project_id:
        request["bridge_project_id"] = project_id
    request_text = _canonical_json(request)
    request_sha = _sha256_text(request_text)
    # A prepared round is immutable.  Re-running the same parent command after
    # the task has been attached must return the original receipt instead of
    # letting the router's now-reuse policy rewrite the handoff.
    if request_path.exists() or handoff_path.exists() or receipt_path.exists():
        if any(
            path.is_symlink() for path in (request_path, handoff_path, receipt_path)
        ):
            raise BridgeError(f"Preparation output contains a symlink: {output_dir}")
        if not (request_path.is_file() and handoff_path.is_file() and receipt_path.is_file()):
            raise BridgeError(f"Preparation output is incomplete: {output_dir}")
        if request_path.read_text(encoding="utf-8") != request_text:
            raise BridgeError(f"Existing preparation request conflicts: {request_path}")
        try:
            existing_handoff_text = handoff_path.read_text(encoding="utf-8")
            existing_receipt_text = receipt_path.read_text(encoding="utf-8")
            existing_handoff = json.loads(existing_handoff_text)
            existing_receipt = json.loads(existing_receipt_text)
        except (OSError, json.JSONDecodeError) as exc:
            raise BridgeError(f"Existing preparation output is invalid: {output_dir}") from exc
        if not isinstance(existing_receipt, dict):
            raise BridgeError(f"Existing preparation receipt is not an object: {receipt_path}")
        if existing_handoff_text != _canonical_json(existing_handoff):
            raise BridgeError(f"Existing preparation handoff is not canonical: {handoff_path}")
        validate_handoff(existing_handoff, require_new_round=True)
        if existing_handoff.get("request_sha256") != request_sha:
            raise BridgeError(f"Existing preparation handoff conflicts: {handoff_path}")
        expected_fields = {
            "bridge_project_id": project_id,
            "remote_project_id": remote_id,
            "target_project_url": remote_url,
            "requested_model": requested_model,
            "model_selection_kind": selection_kind,
            "requested_thinking_intensity": requested_intensity,
            "context_policy": context_policy,
            "max_files": max_files,
            "attachment_policy": "none" if context_policy == "none" else "bundle",
        }
        for field, expected in expected_fields.items():
            if existing_handoff.get(field, "") != expected:
                raise BridgeError(
                    f"Existing preparation handoff {field} conflicts with current input"
                )
        existing_handoff_sha = _sha256_text(existing_handoff_text)
        if existing_receipt.get("handoff_sha256") != existing_handoff_sha:
            raise BridgeError(f"Existing preparation receipt conflicts with handoff: {receipt_path}")
        expected_receipt_fields = {
            "status": "ready",
            "stage": "prepared",
            "schema_version": HANDOFF_V2,
            "bridge_thread_id": thread_id,
            "bridge_project_id": project_id,
            "remote_project_id": remote_id,
            "target_project_url": remote_url,
            "route_scope": route.scope,
            "request_file": str(request_path),
            "request_sha256": request_sha,
            "handoff_file": str(handoff_path),
            "executor": "bridge_executor",
            "requested_model": requested_model,
            "model_selection_kind": selection_kind,
            "requested_thinking_intensity": requested_intensity,
            "context_policy": context_policy,
            "max_files": max_files,
            "attachment_policy": "none" if context_policy == "none" else "bundle",
            "allowed_external_actions": [
                *(["verify-project-identity"] if project_id else []),
                *([] if context_policy == "none" else ["upload-task-bundle"]),
                "send-once",
                "capture-reply",
            ],
            "expected_output_dir": str(output_dir),
            "next_action": "spawn bridge_executor with executor_handoff.json",
        }
        for field, expected in expected_receipt_fields.items():
            if existing_receipt.get(field) != expected:
                raise BridgeError(
                    f"Existing preparation receipt {field} conflicts with current input"
                )
        sidecars = {
            "request.json.sha256": f"{request_sha}  request.json\n",
            "executor_handoff.json.sha256": f"{existing_handoff_sha}  executor_handoff.json\n",
        }
        for name, expected in sidecars.items():
            path = output_dir / name
            if path.is_symlink() or not path.is_file() or path.read_text(encoding="utf-8") != expected:
                raise BridgeError(f"Existing preparation checksum sidecar conflicts: {path}")
        print(json.dumps(existing_receipt, ensure_ascii=False, indent=2, sort_keys=True))
        return existing_receipt
    handoff = {
        "schema_version": HANDOFF_V2,
        "mode": "prepare-and-run",
        "repo": str(repo),
        "bridge_thread_id": thread_id,
        "bridge_project_id": project_id,
        "remote_project_id": remote_id,
        "target_project_url": remote_url,
        "conversation_url": conversation_url,
        "binding_action": binding_action,
        "route_scope": route.scope,
        "thread_policy": route.thread_policy,
        "conversation_policy": route.conversation_policy,
        "request_file": str(request_path),
        "request_sha256": request_sha,
        "business_deadline": args.business_deadline or None,
        "allow_send": True,
        "allowed_external_actions": [
            *(["verify-project-identity"] if project_id else []),
            *([] if context_policy == "none" else ["upload-task-bundle"]),
            "send-once",
            "capture-reply",
        ],
        "expected_output_dir": str(output_dir),
        "requested_model": requested_model,
        "model_selection_kind": selection_kind,
        "requested_thinking_intensity": requested_intensity,
        "context_policy": context_policy,
        "max_files": max_files,
        "attachment_policy": "none" if context_policy == "none" else "bundle",
    }
    validate_handoff(handoff, require_new_round=True)
    handoff_text = _canonical_json(handoff)
    handoff_sha = _sha256_text(handoff_text)
    prepared_at = now_iso()
    if receipt_path.exists():
        try:
            existing_receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            raise BridgeError(f"Existing preparation receipt is invalid: {receipt_path}") from exc
        if existing_receipt.get("handoff_sha256") != handoff_sha:
            raise BridgeError(f"Existing preparation receipt conflicts with handoff: {receipt_path}")
        prepared_at = str(existing_receipt.get("prepared_at", "")) or prepared_at
    receipt = {
        "status": "ready",
        "stage": "prepared",
        "schema_version": HANDOFF_V2,
        "bridge_thread_id": thread_id,
        "bridge_project_id": project_id,
        "remote_project_id": remote_id,
        "target_project_url": remote_url,
        "binding_action": binding_action,
        "route_scope": route.scope,
        "thread_policy": route.thread_policy,
        "conversation_policy": route.conversation_policy,
        "request_file": str(request_path),
        "request_sha256": request_sha,
        "handoff_file": str(handoff_path),
        "handoff_sha256": handoff_sha,
        "executor": "bridge_executor",
        "requested_model": requested_model,
        "model_selection_kind": selection_kind,
        "requested_thinking_intensity": requested_intensity,
        "context_policy": context_policy,
        "max_files": max_files,
        "attachment_policy": "none" if context_policy == "none" else "bundle",
        "allowed_external_actions": handoff["allowed_external_actions"],
        "expected_output_dir": str(output_dir),
        "next_action": "spawn bridge_executor with executor_handoff.json",
        "prepared_at": prepared_at,
    }
    receipt_text = _canonical_json(receipt)
    _publish_transaction(
        output_dir,
        {
            "request.json": request_text,
            "executor_handoff.json": handoff_text,
            "receipt.json": receipt_text,
            "request.json.sha256": request_sha + "  request.json\n",
            "executor_handoff.json.sha256": handoff_sha + "  executor_handoff.json\n",
        },
    )
    print(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True))
    return receipt


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--repo", required=True)
    parser.add_argument("--goal", required=True)
    parser.add_argument("--task", default="")
    parser.add_argument("--question-file", required=True)
    parser.add_argument("--notes", required=True)
    parser.add_argument("--file", dest="files", action="append", default=[])
    parser.add_argument("--bridge-project-id", default="")
    parser.add_argument("--target-project-url", default="")
    parser.add_argument("--remote-project-id", default="")
    parser.add_argument("--standalone", action="store_true")
    parser.add_argument("--mode", default="implementation_check")
    parser.add_argument(
        "--context-policy",
        choices=CONTEXT_POLICIES,
        default="explicit",
        help="冻结仓库证据范围；none 表示纯咨询且不附加仓库文件",
    )
    parser.add_argument(
        "--max-files",
        type=int,
        default=24,
        help="auto context 的最大文件数；explicit/none 会按冻结策略计算",
    )
    parser.add_argument("--requested-model", required=True)
    parser.add_argument(
        "--model-selection-kind", choices=MODEL_SELECTION_KINDS, required=True
    )
    parser.add_argument("--requested-thinking-intensity", required=True)
    parser.add_argument("--business-deadline", default="")
    parser.add_argument("--allow-send", action="store_true")
    return parser


def main() -> int:
    parser = build_parser()
    try:
        prepare(parser.parse_args())
        return 0
    except (BridgeError, OSError, ValueError) as exc:
        parser.error(str(exc))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
