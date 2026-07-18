#!/usr/bin/env python3
"""Explainable routing across local-only, standalone, and Project Bridge work."""

from __future__ import annotations

import datetime as dt
import hashlib
import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

from bridge_store import (
    BridgeError,
    bridge_root,
    default_gpt_session_id,
    parse_metadata,
    validate_id,
)
from project_store import BridgeProjectStore


ROUTE_SCOPES = {"auto", "local_only", "standalone", "project"}
EVIDENCE_MODES = {"none", "explicit", "auto"}


def _normalized_text(value: str) -> str:
    return re.sub(r"\s+", " ", value or "").strip().casefold()


def _slug(value: str, fallback: str = "task") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or fallback


def _bounded_id(value: str) -> str:
    value = re.sub(r"-+", "-", value).strip("-")
    if len(value) <= 80:
        return validate_id(value, "bridge thread id")
    digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:8]
    return validate_id(f"{value[:70].rstrip('-')}-{digest}", "bridge thread id")


def suggest_thread_id(repo: Path, task: str, *, project_id: str = "") -> str:
    prefix = _slug(project_id or repo.name, "project")
    date = dt.datetime.now().astimezone().strftime("%Y%m%d")
    task_slug = _slug(task)[:42].strip("-") or "task"
    base = _bounded_id(f"{prefix}-{date}-{task_slug}")
    bridge_dir = bridge_root(repo)
    project_task_ids = (
        set(BridgeProjectStore(repo).task_states(project_id))
        if project_id
        else set()
    )
    if not (
        (bridge_dir / "threads" / f"{base}.jsonl").exists()
        or (bridge_dir / "threads" / f"{base}.md").exists()
        or base in project_task_ids
    ):
        return base
    for number in range(2, 100):
        candidate = _bounded_id(f"{base}-{number}")
        if not (
            (bridge_dir / "threads" / f"{candidate}.jsonl").exists()
            or (bridge_dir / "threads" / f"{candidate}.md").exists()
            or candidate in project_task_ids
        ):
            return candidate
    raise BridgeError("Could not allocate a unique Bridge Thread id")


@dataclass(frozen=True)
class RouteDecision:
    scope: str
    bridge_project_id: str
    bridge_thread_id: str
    thread_policy: str
    conversation_policy: str
    evidence_mode: str
    remote_project_id: str
    remote_project_url: str
    binding_status: str
    confidence: float
    reason: str
    requires_confirmation: tuple[str, ...] = ()
    expected_workspace: str = ""
    expected_account_label: str = ""

    def to_dict(self) -> Dict[str, Any]:
        result = asdict(self)
        result["requires_confirmation"] = list(self.requires_confirmation)
        return result


def resolve_route(
    repo: Path,
    *,
    task: str,
    requested_scope: str = "auto",
    requires_external_reasoning: bool = True,
    bridge_thread_id: str = "",
    bridge_project_id: str = "",
    evidence_mode: str = "auto",
    force_new_thread: bool = False,
    attach: bool = False,
) -> RouteDecision:
    """Resolve one task without hiding ambiguity or mutating by default."""
    repo = repo.resolve()
    if not repo.is_dir():
        raise BridgeError(f"Repository root is not a directory: {repo}")
    requested_scope = requested_scope.replace("-", "_")
    if requested_scope not in ROUTE_SCOPES:
        raise BridgeError(f"scope must be one of: {', '.join(sorted(ROUTE_SCOPES))}")
    if evidence_mode not in EVIDENCE_MODES:
        raise BridgeError(
            f"evidence mode must be one of: {', '.join(sorted(EVIDENCE_MODES))}"
        )
    if requested_scope == "local_only":
        requires_external_reasoning = False
    if not requires_external_reasoning:
        return RouteDecision(
            scope="local_only",
            bridge_project_id="",
            bridge_thread_id="",
            thread_policy="none",
            conversation_policy="none",
            evidence_mode="none",
            remote_project_id="",
            remote_project_url="",
            binding_status="not-applicable",
            confidence=1.0 if requested_scope != "auto" else 0.96,
            reason="The task does not require an external GPT Pro reasoning round.",
        )

    store = BridgeProjectStore(repo)
    project_ids = store.list_project_ids()
    current_project_id = ""
    if bridge_project_id:
        current_project_id = store.resolve_project_id(bridge_project_id)
    elif len(project_ids) == 1:
        current_project_id = project_ids[0]
    elif len(project_ids) > 1:
        raise BridgeError(
            "Multiple Bridge Projects are visible; specify --bridge-project-id"
        )

    supplied_thread = (
        validate_id(bridge_thread_id, "bridge thread id") if bridge_thread_id else ""
    )
    if supplied_thread and force_new_thread:
        raise BridgeError(
            "--force-new-thread cannot be combined with an explicit Bridge Thread id"
        )
    attached_project = (
        store.project_for_thread(supplied_thread)
        if supplied_thread and current_project_id
        else ""
    )
    if (
        supplied_thread
        and attached_project
        and current_project_id
        and attached_project != current_project_id
    ):
        raise BridgeError(
            f"Bridge Thread {supplied_thread} belongs to {attached_project}, "
            f"not {current_project_id}"
        )

    scope = requested_scope
    reason = ""
    confidence = 1.0
    if scope == "auto":
        if attached_project:
            scope = "project"
            reason = "The named Bridge Thread is already attached to the local Bridge Project."
            confidence = 0.99
        elif supplied_thread:
            scope = "standalone"
            reason = "The named Bridge Thread exists outside Project membership."
            confidence = 0.98
        elif current_project_id:
            scope = "project"
            reason = (
                "The current repository has one Bridge Project; its binding must be "
                "used or repaired rather than bypassed."
            )
            confidence = 0.96
        else:
            scope = "standalone"
            reason = "No active Project binding is available, so the external review stays standalone."
            confidence = 0.94
    elif scope == "project":
        reason = "Project mode was explicitly requested."
    else:
        reason = "Standalone mode was explicitly requested."

    if scope == "project" and not current_project_id:
        return RouteDecision(
            scope="project",
            bridge_project_id="",
            bridge_thread_id=supplied_thread,
            thread_policy="blocked",
            conversation_policy="blocked",
            evidence_mode=evidence_mode,
            remote_project_id="",
            remote_project_url="",
            binding_status="missing",
            confidence=confidence,
            reason=reason,
            requires_confirmation=("create-or-bind-bridge-project",),
        )
    if scope == "project":
        current_project = store.load_project(current_project_id)
        if current_project.get("status") != "active":
            archived_binding = store.load_binding(current_project_id)
            return RouteDecision(
                scope="project",
                bridge_project_id=current_project_id,
                bridge_thread_id=supplied_thread,
                thread_policy="blocked",
                conversation_policy="blocked",
                evidence_mode=evidence_mode,
                remote_project_id=str(
                    archived_binding.get("remote_project_id", "")
                ),
                remote_project_url=str(
                    archived_binding.get("remote_project_url", "")
                ),
                binding_status=str(
                    archived_binding.get("status", "missing")
                ),
                confidence=confidence,
                reason=(
                    reason
                    + " The local Bridge Project is archived and must be "
                    "reactivated before Project work resumes."
                ),
                requires_confirmation=("reactivate-bridge-project",),
                expected_workspace=str(
                    archived_binding.get("workspace", "")
                ),
                expected_account_label=str(
                    archived_binding.get("account_label", "")
                ),
            )
    if scope == "standalone" and attached_project:
        raise BridgeError(
            f"Bridge Thread {supplied_thread} is Project-bound and cannot be routed standalone"
        )

    project_tasks: Dict[str, Dict[str, Any]] = {}
    binding: Dict[str, Any] = {}
    if scope == "project":
        project_tasks = store.task_states(current_project_id)
        binding = store.load_binding(current_project_id)

    selected_thread = supplied_thread
    existing_task = False
    if scope == "project" and not selected_thread and not force_new_thread:
        normalized_task = _normalized_text(task)
        matches = [
            item
            for item in project_tasks.values()
            if item.get("status") not in {"archived"}
            and normalized_task
            and normalized_task
            in {
                _normalized_text(str(item.get("goal", ""))),
                _normalized_text(str(item.get("title", ""))),
            }
        ]
        if len(matches) > 1:
            raise BridgeError(
                "Multiple active Bridge Tasks match this exact title or goal; "
                "specify --bridge-thread-id or use --force-new-thread"
            )
        if matches:
            selected_thread = str(matches[0]["bridge_thread_id"])
            existing_task = True
            reason += " An existing Bridge Task has the same normalized goal or title."
    if not selected_thread:
        selected_thread = suggest_thread_id(
            repo, task, project_id=current_project_id if scope == "project" else ""
        )

    thread_exists = bool(
        (bridge_root(repo) / "threads" / f"{selected_thread}.jsonl").exists()
        or (bridge_root(repo) / "threads" / f"{selected_thread}.md").exists()
    )
    if scope == "project":
        existing_task = existing_task or selected_thread in project_tasks
        thread_policy = (
            "reuse"
            if existing_task
            else "promote"
            if thread_exists
            else "create"
        )
    else:
        thread_policy = "reuse" if thread_exists else "create"

    gpt_session_id = default_gpt_session_id(selected_thread)
    session_meta = parse_metadata(
        bridge_root(repo) / "gpt-pro-sessions" / gpt_session_id / "session.md"
    )
    conversation_policy = (
        "reuse" if session_meta.get("web_conversation_url") else "create"
    )
    confirmations: list[str] = []
    binding_status = "not-applicable"
    remote_project_id = ""
    remote_project_url = ""
    if scope == "project":
        binding_status = str(binding.get("status", "missing")) if binding else "missing"
        remote_project_id = str(binding.get("remote_project_id", ""))
        remote_project_url = str(binding.get("remote_project_url", ""))
        if binding_status != "active":
            confirmations.append("verify-or-bind-chatgpt-project")
            conversation_policy = "blocked"
        else:
            project_report = store.verify(current_project_id)
            if project_report["unsynced_source_count"]:
                confirmations.append("sync-project-sources")
                conversation_policy = "blocked"
                reason += (
                    f" {project_report['unsynced_source_count']} Project Source "
                    "records are not synchronized."
                )
            elif not project_report["inventory_verified"]:
                confirmations.append("verify-project-source-inventory")
                conversation_policy = "blocked"
                reason += (
                    " The recorded Project Sources have not been verified "
                    "against a current complete remote inventory."
                )
        if session_meta.get("remote_project_id") not in (
            None,
            "",
            remote_project_id,
        ):
            raise BridgeError(
                f"GPT Pro session {gpt_session_id} belongs to another ChatGPT Project"
            )
        if (
            binding_status == "active"
            and session_meta.get("web_conversation_url")
            and not session_meta.get("remote_project_id")
        ):
            conversation_policy = "verify-or-rehome"
            reason += (
                " The existing standalone conversation must be visibly moved "
                "into this Project before it can be reused."
            )
    elif session_meta.get("remote_project_id"):
        raise BridgeError(
            f"GPT Pro session {gpt_session_id} is Project-bound and cannot be standalone"
        )

    decision = RouteDecision(
        scope=scope,
        bridge_project_id=current_project_id if scope == "project" else "",
        bridge_thread_id=selected_thread,
        thread_policy=thread_policy,
        conversation_policy=conversation_policy,
        evidence_mode=evidence_mode,
        remote_project_id=remote_project_id,
        remote_project_url=remote_project_url,
        binding_status=binding_status,
        confidence=confidence,
        reason=reason,
        requires_confirmation=tuple(confirmations),
        expected_workspace=(
            str(binding.get("workspace", "")) if scope == "project" else ""
        ),
        expected_account_label=(
            str(binding.get("account_label", "")) if scope == "project" else ""
        ),
    )
    if attach and scope == "project" and not confirmations:
        store.attach_thread(
            current_project_id,
            selected_thread,
            title=task,
            goal=task,
            status="active",
        )
    return decision
