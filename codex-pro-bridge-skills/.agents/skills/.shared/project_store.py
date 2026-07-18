#!/usr/bin/env python3
"""Project-aware persistence for Codex Pro Bridge.

Bridge Thread ledgers remain the canonical history for individual tasks. This
module owns the smaller project-level model: one local project, at most one
ChatGPT Project binding, shared source state, and task membership.
"""

from __future__ import annotations

import datetime as dt
import hashlib
import json
import os
import re
import uuid
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence
from urllib.parse import urlparse, urlunparse

from bridge_store import (
    BridgeError,
    atomic_write_text,
    bridge_root,
    file_lock,
    file_sha256,
    load_events,
    now_iso,
    repo_relative,
    resolve_repo_path,
    validate_id,
)


PROJECT_SCHEMA_VERSION = 2
SYNC_MODES = {"read_only", "append_only", "managed"}
BINDING_STATUSES = {
    "unverified",
    "active",
    "stale",
    "missing",
    "account_mismatch",
    "unbound",
}
TASK_STATUSES = {"proposed", "ready", "active", "blocked", "review", "done", "archived"}
SOURCE_SYNC_STATUSES = {"pending", "synced", "stale", "failed", "missing"}
PROJECT_EVENT_TYPES = {
    "project-created",
    "remote-bound",
    "binding-verified",
    "binding-missing",
    "remote-unbound",
    "source-synced",
    "source-inventory-checked",
    "task-attached",
    "task-updated",
    "task-status-changed",
    "project-updated",
    "project-archived",
    "project-reactivated",
}
REMOTE_PROJECT_ID_RE = re.compile(r"^g-p-[A-Za-z0-9_-]+$")
REMOTE_PROJECT_SLUG_RE = re.compile(r"^(g-p-[0-9A-Fa-f]{32})(?:-.+)?$")


def _read_json(path: Path, *, default: Any = None) -> Any:
    if not path.exists():
        return default
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise BridgeError(f"Invalid JSON at {path}: {exc}") from exc


def _write_json(path: Path, value: Mapping[str, Any]) -> None:
    atomic_write_text(
        path,
        json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    )


def _one_line(value: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text


def normalize_remote_project_url(value: str) -> str:
    value = (value or "").strip()
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    allowed = hostname in {"chatgpt.com", "chat.openai.com"} or hostname.endswith(
        (".chatgpt.com", ".chat.openai.com")
    )
    if parsed.scheme != "https" or not allowed:
        raise BridgeError("ChatGPT Project URL must be an https URL on chatgpt.com")
    path = re.sub(r"/+", "/", parsed.path).rstrip("/") or "/"
    return urlunparse(("https", parsed.netloc, path, "", "", ""))


def remote_project_id_from_url(value: str, explicit_id: str = "") -> str:
    if explicit_id:
        project_id = explicit_id.strip()
        if not REMOTE_PROJECT_ID_RE.fullmatch(project_id):
            raise BridgeError("Remote project id must look like g-p-<id>")
        return project_id
    parsed = urlparse(normalize_remote_project_url(value))
    for segment in parsed.path.split("/"):
        slug_match = REMOTE_PROJECT_SLUG_RE.fullmatch(segment)
        if slug_match:
            return slug_match.group(1)
        if REMOTE_PROJECT_ID_RE.fullmatch(segment):
            return segment
    raise BridgeError(
        "Could not determine the ChatGPT Project id from the URL; provide --remote-project-id"
    )


class BridgeProjectStore:
    """Deep module for project identity, binding, tasks, and project audit state."""

    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        if not self.repo.is_dir():
            raise BridgeError(f"Repository root is not a directory: {self.repo}")
        self.bridge_dir = bridge_root(self.repo)
        self.projects_dir = self.bridge_dir / "projects"

    def project_dir(self, project_id: str) -> Path:
        return self.projects_dir / validate_id(project_id, "bridge project id")

    def sources_dir(self, project_id: str) -> Path:
        return self.project_dir(project_id) / "sources"

    def source_manifest_path(self, project_id: str) -> Path:
        return self.sources_dir(project_id) / "manifest.json"

    def source_plans_dir(self, project_id: str) -> Path:
        return self.sources_dir(project_id) / "plans"

    def list_project_ids(self) -> List[str]:
        if not self.projects_dir.exists():
            return []
        return sorted(
            path.parent.name
            for path in self.projects_dir.glob("*/project.json")
            if path.is_file()
        )

    def resolve_project_id(self, project_id: str = "") -> str:
        if project_id:
            project_id = validate_id(project_id, "bridge project id")
            if not (self.project_dir(project_id) / "project.json").is_file():
                raise BridgeError(f"Unknown Bridge Project: {project_id}")
            return project_id
        project_ids = self.list_project_ids()
        if not project_ids:
            raise BridgeError("This repository has no Bridge Project")
        if len(project_ids) > 1:
            raise BridgeError(
                "This repository has multiple Bridge Projects; specify --bridge-project-id"
            )
        return project_ids[0]

    def create_project(
        self,
        project_id: str,
        *,
        title: str = "",
        brief_path: str = "",
    ) -> Dict[str, Any]:
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        with file_lock(self.projects_dir / ".projects.lock"):
            return self._create_project_unlocked(
                project_id,
                title=title,
                brief_path=brief_path,
            )

    def _create_project_unlocked(
        self,
        project_id: str,
        *,
        title: str = "",
        brief_path: str = "",
    ) -> Dict[str, Any]:
        project_id = validate_id(project_id, "bridge project id")
        existing_ids = self.list_project_ids()
        if existing_ids and project_id not in existing_ids:
            raise BridgeError(
                f"Local project {self.repo} is already represented by {existing_ids[0]}"
            )
        directory = self.project_dir(project_id)
        project_path = directory / "project.json"
        existing = _read_json(project_path, default={})
        if existing:
            self._validate_project(existing, expected_id=project_id)
            return existing

        directory.mkdir(parents=True, exist_ok=True)
        if brief_path:
            brief = resolve_repo_path(brief_path, self.repo)
            if not brief.is_file():
                raise BridgeError("Project brief must be a file")
        else:
            brief = directory / "PROJECT_BRIEF.md"
            atomic_write_text(
                brief,
                "\n".join(
                    [
                        f"# {title.strip() or project_id}",
                        "",
                        "## Goal",
                        "",
                        "_Describe the long-running project objective._",
                        "",
                        "## Constraints",
                        "",
                        "_Record stable project constraints._",
                        "",
                        "## Current Frontier",
                        "",
                        "_Record the next important project decision._",
                        "",
                    ]
                ),
            )
        created_at = now_iso()
        project = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "bridge_project_id": project_id,
            "title": _one_line(title or project_id, 120),
            "local_root": str(self.repo),
            "brief_path": repo_relative(brief, self.repo),
            "status": "active",
            "created_at": created_at,
            "updated_at": created_at,
        }
        _write_json(project_path, project)
        self.append_activity(
            project_id,
            "project-created",
            {
                "title": project["title"],
                "local_root": project["local_root"],
                "brief_path": project["brief_path"],
            },
            dedupe_key=f"project-created:{project_id}",
        )
        self._write_index()
        return project

    def load_project(self, project_id: str = "") -> Dict[str, Any]:
        project_id = self.resolve_project_id(project_id)
        project = _read_json(self.project_dir(project_id) / "project.json", default={})
        self._validate_project(project, expected_id=project_id)
        return project

    def update_project(
        self,
        project_id: str,
        *,
        title: str | None = None,
        brief_path: str | None = None,
    ) -> Dict[str, Any]:
        project_id = self.resolve_project_id(project_id)
        with file_lock(self.project_dir(project_id) / ".project.lock"):
            project = self.load_project(project_id)
            changes: Dict[str, Any] = {}
            if title is not None:
                next_title = _one_line(title or project_id, 120)
                if next_title != project["title"]:
                    changes["title"] = next_title
            if brief_path is not None:
                brief = resolve_repo_path(brief_path, self.repo)
                if not brief.is_file():
                    raise BridgeError("Project brief must be a file")
                next_brief_path = repo_relative(brief, self.repo)
                if next_brief_path != project["brief_path"]:
                    changes["brief_path"] = next_brief_path
            if not changes:
                return project
            now = now_iso()
            project.update(changes)
            project["updated_at"] = now
            _write_json(self.project_dir(project_id) / "project.json", project)
            fingerprint = hashlib.sha256(
                json.dumps(changes, ensure_ascii=False, sort_keys=True).encode(
                    "utf-8"
                )
            ).hexdigest()
            self.append_activity(
                project_id,
                "project-updated",
                changes,
                dedupe_key=f"project-updated:{fingerprint}",
            )
            self._write_index()
            return project

    def archive_project(
        self,
        project_id: str,
        *,
        reason: str = "",
    ) -> Dict[str, Any]:
        project_id = self.resolve_project_id(project_id)
        with file_lock(self.project_dir(project_id) / ".project.lock"):
            project = self.load_project(project_id)
            if project["status"] == "archived":
                return project
            now = now_iso()
            project["status"] = "archived"
            project["archived_at"] = now
            project["archive_reason"] = _one_line(reason, 240)
            project["updated_at"] = now
            _write_json(self.project_dir(project_id) / "project.json", project)
            self.append_activity(
                project_id,
                "project-archived",
                {"reason": project["archive_reason"]},
                dedupe_key=f"project-archived:{now}",
            )
            self._write_index()
            return project

    def reactivate_project(self, project_id: str) -> Dict[str, Any]:
        project_id = self.resolve_project_id(project_id)
        with file_lock(self.project_dir(project_id) / ".project.lock"):
            project = self.load_project(project_id)
            if project["status"] == "active":
                return project
            now = now_iso()
            project["status"] = "active"
            project["reactivated_at"] = now
            project["updated_at"] = now
            _write_json(self.project_dir(project_id) / "project.json", project)
            self.append_activity(
                project_id,
                "project-reactivated",
                {},
                dedupe_key=f"project-reactivated:{now}",
            )
            self._write_index()
            return project

    def load_binding(self, project_id: str = "") -> Dict[str, Any]:
        project_id = self.resolve_project_id(project_id)
        binding = _read_json(
            self.project_dir(project_id) / "remote-binding.json", default={}
        )
        if binding:
            self._validate_binding(binding, expected_project_id=project_id)
        return binding

    def bind_remote(
        self,
        project_id: str,
        *,
        remote_url: str,
        remote_project_id: str = "",
        observed_title: str = "",
        workspace: str = "",
        account_label: str = "",
        sync_mode: str = "append_only",
        max_project_files: int = 0,
        verified: bool = False,
        allow_rebind: bool = False,
    ) -> Dict[str, Any]:
        project_id = self.resolve_project_id(project_id)
        with file_lock(self.project_dir(project_id) / ".binding.lock"):
            return self._bind_remote_unlocked(
                project_id,
                remote_url=remote_url,
                remote_project_id=remote_project_id,
                observed_title=observed_title,
                workspace=workspace,
                account_label=account_label,
                sync_mode=sync_mode,
                max_project_files=max_project_files,
                verified=verified,
                allow_rebind=allow_rebind,
            )

    def _bind_remote_unlocked(
        self,
        project_id: str,
        *,
        remote_url: str,
        remote_project_id: str = "",
        observed_title: str = "",
        workspace: str = "",
        account_label: str = "",
        sync_mode: str = "append_only",
        max_project_files: int = 0,
        verified: bool = False,
        allow_rebind: bool = False,
    ) -> Dict[str, Any]:
        project = self.load_project(project_id)
        project_id = project["bridge_project_id"]
        if project["status"] != "active":
            raise BridgeError(
                f"Bridge Project {project_id} is archived; reactivate it before binding"
            )
        if sync_mode not in SYNC_MODES:
            raise BridgeError(f"sync mode must be one of: {', '.join(sorted(SYNC_MODES))}")
        if max_project_files < 0:
            raise BridgeError("max project files cannot be negative")
        if verified and (not workspace.strip() or not account_label.strip()):
            raise BridgeError(
                "A verified binding requires observed workspace and account labels"
            )
        remote_url = normalize_remote_project_url(remote_url)
        remote_project_id = remote_project_id_from_url(remote_url, remote_project_id)

        for other_id in self.list_project_ids():
            if other_id == project_id:
                continue
            other = self.load_binding(other_id)
            if (
                other.get("status") != "unbound"
                and other.get("remote_project_id") == remote_project_id
            ):
                raise BridgeError(
                    f"ChatGPT Project {remote_project_id} is already bound to {other_id}"
                )

        previous = self.load_binding(project_id)
        previous_remote = previous.get("remote_project_id", "")
        if (
            previous_remote
            and previous.get("status") != "unbound"
            and previous_remote != remote_project_id
            and not allow_rebind
        ):
            raise BridgeError(
                f"{project_id} is already bound to {previous_remote}; use an explicit rebind"
            )
        now = now_iso()
        same_remote = (
            previous_remote == remote_project_id
            and previous.get("status") != "unbound"
        )
        next_workspace = _one_line(
            workspace or (previous.get("workspace", "") if same_remote else ""), 160
        )
        next_account_label = _one_line(
            account_label
            or (previous.get("account_label", "") if same_remote else ""),
            160,
        )
        retained_verification = bool(
            same_remote
            and previous.get("status") == "active"
            and next_workspace == previous.get("workspace", "")
            and next_account_label == previous.get("account_label", "")
        )
        active = verified or retained_verification
        binding = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "bridge_project_id": project_id,
            "provider": "chatgpt-web",
            "remote_project_id": remote_project_id,
            "remote_project_url": remote_url,
            "observed_title": _one_line(
                observed_title or (previous.get("observed_title", "") if same_remote else ""),
                160,
            ),
            "workspace": next_workspace,
            "account_label": next_account_label,
            "sync_mode": sync_mode,
            "status": "active" if active else "unverified",
            "max_project_files": max_project_files
            or (previous.get("max_project_files", 0) if same_remote else 0),
            "bound_at": previous.get("bound_at", "") if same_remote else now,
            "last_checked_at": (
                now
                if verified
                else previous.get("last_checked_at", "")
                if retained_verification
                else ""
            ),
            "last_verified_at": (
                now
                if verified
                else previous.get("last_verified_at", "")
                if retained_verification
                else ""
            ),
            "updated_at": now,
        }
        _write_json(self.project_dir(project_id) / "remote-binding.json", binding)
        binding_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "remote_project_id": remote_project_id,
                    "remote_project_url": remote_url,
                    "observed_title": binding["observed_title"],
                    "workspace": binding["workspace"],
                    "account_label": binding["account_label"],
                    "sync_mode": sync_mode,
                    "max_project_files": binding["max_project_files"],
                    "status": binding["status"],
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        self.append_activity(
            project_id,
            "remote-bound",
            {
                "remote_project_id": remote_project_id,
                "remote_project_url": remote_url,
                "sync_mode": sync_mode,
                "status": binding["status"],
                "rebind": bool(previous_remote and previous_remote != remote_project_id),
            },
            dedupe_key=f"remote-bound:{remote_project_id}:{binding_fingerprint}",
        )
        if verified:
            self.append_activity(
                project_id,
                "binding-verified",
                {
                    "remote_project_id": remote_project_id,
                    "status": "active",
                    "workspace": binding["workspace"],
                    "account_label": binding["account_label"],
                },
                dedupe_key=f"binding-verified:{remote_project_id}:{now}",
            )
        self._render_overview(project_id)
        return binding

    def verify_remote_binding(
        self,
        project_id: str,
        *,
        observed_url: str,
        observed_project_id: str = "",
        observed_title: str = "",
        workspace: str = "",
        account_label: str = "",
    ) -> Dict[str, Any]:
        project_id = self.resolve_project_id(project_id)
        with file_lock(self.project_dir(project_id) / ".binding.lock"):
            return self._verify_remote_binding_unlocked(
                project_id,
                observed_url=observed_url,
                observed_project_id=observed_project_id,
                observed_title=observed_title,
                workspace=workspace,
                account_label=account_label,
            )

    def _verify_remote_binding_unlocked(
        self,
        project_id: str,
        *,
        observed_url: str,
        observed_project_id: str = "",
        observed_title: str = "",
        workspace: str = "",
        account_label: str = "",
    ) -> Dict[str, Any]:
        binding = self.load_binding(project_id)
        if not binding or binding.get("status") == "unbound":
            raise BridgeError(f"{project_id} has no current ChatGPT Project binding")
        observed_url = normalize_remote_project_url(observed_url)
        observed_project_id = remote_project_id_from_url(
            observed_url, observed_project_id
        )
        if not workspace.strip() or not account_label.strip():
            raise BridgeError(
                "Binding verification requires observed workspace and account labels"
            )
        mismatches: List[str] = []
        if observed_project_id != binding["remote_project_id"]:
            mismatches.append(
                f"project id {observed_project_id} != {binding['remote_project_id']}"
            )
        expected_workspace = binding.get("workspace", "")
        if expected_workspace and workspace and expected_workspace != workspace:
            mismatches.append(f"workspace {workspace!r} != {expected_workspace!r}")
        expected_account = binding.get("account_label", "")
        if expected_account and account_label and expected_account != account_label:
            mismatches.append(f"account {account_label!r} != {expected_account!r}")

        now = now_iso()
        binding["last_observed_project_id"] = observed_project_id
        binding["last_observed_project_url"] = observed_url
        binding["last_observed_title"] = _one_line(observed_title, 160)
        binding["last_observed_workspace"] = _one_line(workspace, 160)
        binding["last_observed_account_label"] = _one_line(account_label, 160)
        binding["last_checked_at"] = now
        binding["updated_at"] = now
        if mismatches:
            binding["status"] = (
                "account_mismatch"
                if any(item.startswith(("workspace ", "account ")) for item in mismatches)
                else "stale"
            )
        else:
            binding["remote_project_url"] = observed_url
            binding["observed_title"] = _one_line(
                observed_title or binding.get("observed_title", ""), 160
            )
            binding["workspace"] = _one_line(workspace or expected_workspace, 160)
            binding["account_label"] = _one_line(
                account_label or expected_account, 160
            )
            binding["status"] = "active"
            binding["last_verified_at"] = now
        _write_json(self.project_dir(project_id) / "remote-binding.json", binding)
        self.append_activity(
            project_id,
            "binding-verified",
            {
                "remote_project_id": observed_project_id,
                "status": binding["status"],
                "mismatches": mismatches,
                "workspace": workspace,
                "account_label": account_label,
            },
            dedupe_key=(
                f"binding-verified:{observed_project_id}:"
                + hashlib.sha256(
                    json.dumps(
                        {
                            "status": binding["status"],
                            "mismatches": mismatches,
                            "title": observed_title,
                            "workspace": workspace,
                            "account": account_label,
                        },
                        ensure_ascii=False,
                        sort_keys=True,
                    ).encode("utf-8")
                ).hexdigest()
            ),
        )
        self._render_overview(project_id)
        if mismatches:
            raise BridgeError("ChatGPT Project binding verification failed: " + "; ".join(mismatches))
        return binding

    def mark_remote_missing(
        self, project_id: str, *, reason: str = ""
    ) -> Dict[str, Any]:
        project_id = self.resolve_project_id(project_id)
        with file_lock(self.project_dir(project_id) / ".binding.lock"):
            return self._mark_remote_missing_unlocked(project_id, reason=reason)

    def _mark_remote_missing_unlocked(
        self, project_id: str, *, reason: str = ""
    ) -> Dict[str, Any]:
        binding = self.load_binding(project_id)
        if not binding or binding.get("status") == "unbound":
            raise BridgeError(f"{project_id} has no current ChatGPT Project binding")
        now = now_iso()
        binding["status"] = "missing"
        binding["missing_at"] = now
        binding["missing_reason"] = _one_line(reason, 240)
        binding["last_checked_at"] = now
        binding["updated_at"] = now
        _write_json(self.project_dir(project_id) / "remote-binding.json", binding)
        self.append_activity(
            project_id,
            "binding-missing",
            {
                "remote_project_id": binding.get("remote_project_id", ""),
                "reason": binding["missing_reason"],
            },
            dedupe_key=f"binding-missing:{binding.get('remote_project_id', '')}:{now}",
        )
        self._render_overview(project_id)
        return binding

    def unbind_remote(self, project_id: str, *, reason: str = "") -> Dict[str, Any]:
        project_id = self.resolve_project_id(project_id)
        with file_lock(self.project_dir(project_id) / ".binding.lock"):
            return self._unbind_remote_unlocked(project_id, reason=reason)

    def _unbind_remote_unlocked(
        self, project_id: str, *, reason: str = ""
    ) -> Dict[str, Any]:
        binding = self.load_binding(project_id)
        if not binding:
            raise BridgeError(f"{project_id} has no ChatGPT Project binding")
        if binding.get("status") == "unbound":
            return binding
        now = now_iso()
        binding["status"] = "unbound"
        binding["unbound_at"] = now
        binding["unbind_reason"] = _one_line(reason, 240)
        binding["updated_at"] = now
        _write_json(self.project_dir(project_id) / "remote-binding.json", binding)
        self.append_activity(
            project_id,
            "remote-unbound",
            {
                "remote_project_id": binding.get("remote_project_id", ""),
                "reason": binding["unbind_reason"],
            },
            dedupe_key=f"remote-unbound:{binding.get('remote_project_id', '')}:{now}",
        )
        self._render_overview(project_id)
        return binding

    def load_activity(self, project_id: str = "") -> List[Dict[str, Any]]:
        project_id = self.resolve_project_id(project_id)
        path = self.project_dir(project_id) / "activity.jsonl"
        if not path.exists():
            return []
        events: List[Dict[str, Any]] = []
        for line_number, line in enumerate(
            path.read_text(encoding="utf-8").splitlines(), start=1
        ):
            if not line.strip():
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError as exc:
                raise BridgeError(f"Invalid JSONL at {path}:{line_number}: {exc}") from exc
            if event.get("bridge_project_id") != project_id:
                raise BridgeError(f"Project id mismatch at {path}:{line_number}")
            events.append(event)
        return events

    def append_activity(
        self,
        project_id: str,
        event_type: str,
        data: Mapping[str, Any],
        *,
        dedupe_key: str = "",
        occurred_at: str = "",
    ) -> Dict[str, Any]:
        project_id = validate_id(project_id, "bridge project id")
        if event_type not in PROJECT_EVENT_TYPES:
            raise BridgeError(f"Unsupported project event type: {event_type}")
        directory = self.project_dir(project_id)
        if not (directory / "project.json").is_file():
            raise BridgeError(f"Unknown Bridge Project: {project_id}")
        path = directory / "activity.jsonl"
        with file_lock(directory / ".activity.lock"):
            events = self.load_activity(project_id)
            if dedupe_key:
                for event in events:
                    if event.get("dedupe_key") == dedupe_key:
                        return event
            timestamp = occurred_at or now_iso()
            event = {
                "schema_version": PROJECT_SCHEMA_VERSION,
                "event_id": (
                    f"{timestamp.replace(':', '').replace('+', '-')}-"
                    f"{uuid.uuid4().hex[:10]}"
                ),
                "bridge_project_id": project_id,
                "event_type": event_type,
                "occurred_at": timestamp,
                "parent_event_id": events[-1]["event_id"] if events else "",
                "data": dict(data),
                "dedupe_key": dedupe_key,
            }
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            events.append(event)
        self._render_overview(project_id)
        self._write_index()
        return event

    def task_states(self, project_id: str = "") -> Dict[str, Dict[str, Any]]:
        project_id = self.resolve_project_id(project_id)
        tasks: Dict[str, Dict[str, Any]] = {}
        for event in self.load_activity(project_id):
            data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
            thread_id = str(data.get("bridge_thread_id", ""))
            if event.get("event_type") == "task-attached" and thread_id:
                tasks.setdefault(
                    thread_id,
                    {
                        "bridge_thread_id": thread_id,
                        "title": str(data.get("title", "")) or thread_id,
                        "goal": str(data.get("goal", "")),
                        "status": str(data.get("status", "")) or "active",
                        "depends_on": list(data.get("depends_on", [])),
                        "attached_at": event.get("occurred_at", ""),
                        "updated_at": event.get("occurred_at", ""),
                    },
                )
            elif event.get("event_type") == "task-updated" and thread_id in tasks:
                for key in ("title", "goal", "depends_on"):
                    if key in data:
                        tasks[thread_id][key] = (
                            list(data[key])
                            if key == "depends_on"
                            else str(data[key])
                        )
                tasks[thread_id]["updated_at"] = event.get("occurred_at", "")
            elif event.get("event_type") == "task-status-changed" and thread_id in tasks:
                tasks[thread_id]["status"] = str(data.get("status", ""))
                tasks[thread_id]["updated_at"] = event.get("occurred_at", "")
        return tasks

    def attach_thread(
        self,
        project_id: str,
        thread_id: str,
        *,
        title: str = "",
        goal: str = "",
        status: str = "active",
        depends_on: Sequence[str] = (),
    ) -> Dict[str, Any]:
        project_id = self.resolve_project_id(project_id)
        project = self.load_project(project_id)
        if project["status"] != "active":
            raise BridgeError(
                f"Bridge Project {project_id} is archived; reactivate it before attaching tasks"
            )
        thread_id = validate_id(thread_id, "bridge thread id")
        if status not in TASK_STATUSES:
            raise BridgeError(f"task status must be one of: {', '.join(sorted(TASK_STATUSES))}")
        dependencies = [validate_id(item, "dependency thread id") for item in depends_on]
        if thread_id in dependencies:
            raise BridgeError("A Bridge Task cannot depend on itself")

        existing = self.task_states(project_id).get(thread_id)
        if existing:
            return existing
        current_tasks = self.task_states(project_id)
        missing_dependencies = [
            dependency for dependency in dependencies if dependency not in current_tasks
        ]
        if missing_dependencies:
            raise BridgeError(
                f"Bridge Task {thread_id} has unknown dependencies: "
                f"{missing_dependencies}"
            )
        events = load_events(self.bridge_dir, thread_id)
        event_projects = {
            str(event.get("bridge_project_id"))
            for event in events
            if event.get("bridge_project_id")
        }
        if event_projects and event_projects != {project_id}:
            raise BridgeError(
                f"Bridge Thread {thread_id} already belongs to {sorted(event_projects)}"
            )
        self.append_activity(
            project_id,
            "task-attached",
            {
                "bridge_thread_id": thread_id,
                "title": _one_line(title or thread_id, 160),
                "goal": _one_line(goal, 500),
                "status": status,
                "depends_on": dependencies,
            },
            dedupe_key=f"task-attached:{thread_id}",
        )
        return self.task_states(project_id)[thread_id]

    def update_task(
        self,
        project_id: str,
        thread_id: str,
        *,
        title: str | None = None,
        goal: str | None = None,
        depends_on: Sequence[str] | None = None,
    ) -> Dict[str, Any]:
        project_id = self.resolve_project_id(project_id)
        project = self.load_project(project_id)
        if project["status"] != "active":
            raise BridgeError(
                f"Bridge Project {project_id} is archived; reactivate it before updating tasks"
            )
        thread_id = validate_id(thread_id, "bridge thread id")
        tasks = self.task_states(project_id)
        current = tasks.get(thread_id)
        if not current:
            raise BridgeError(
                f"Bridge Thread {thread_id} is not attached to {project_id}"
            )

        changes: Dict[str, Any] = {"bridge_thread_id": thread_id}
        if title is not None:
            changes["title"] = _one_line(title or thread_id, 160)
        if goal is not None:
            changes["goal"] = _one_line(goal, 500)
        if depends_on is not None:
            dependencies = [
                validate_id(item, "dependency thread id")
                for item in depends_on
            ]
            if thread_id in dependencies:
                raise BridgeError("A Bridge Task cannot depend on itself")
            missing = [
                dependency
                for dependency in dependencies
                if dependency not in tasks
            ]
            if missing:
                raise BridgeError(
                    f"Bridge Task {thread_id} has unknown dependencies: {missing}"
                )
            candidate_dependencies = {
                task_id: list(task["depends_on"])
                for task_id, task in tasks.items()
            }
            candidate_dependencies[thread_id] = dependencies
            visiting: set[str] = set()
            visited: set[str] = set()

            def visit(task_id: str) -> None:
                if task_id in visiting:
                    raise BridgeError(
                        f"Bridge Task dependency cycle includes {task_id}"
                    )
                if task_id in visited:
                    return
                visiting.add(task_id)
                for dependency in candidate_dependencies[task_id]:
                    visit(dependency)
                visiting.remove(task_id)
                visited.add(task_id)

            for task_id in candidate_dependencies:
                visit(task_id)
            changes["depends_on"] = dependencies

        if len(changes) == 1 or all(
            current.get(key) == value
            for key, value in changes.items()
            if key != "bridge_thread_id"
        ):
            return current
        fingerprint = hashlib.sha256(
            json.dumps(changes, ensure_ascii=False, sort_keys=True).encode("utf-8")
        ).hexdigest()
        self.append_activity(
            project_id,
            "task-updated",
            changes,
            dedupe_key=f"task-updated:{thread_id}:{fingerprint}",
        )
        return self.task_states(project_id)[thread_id]

    def set_task_status(
        self, project_id: str, thread_id: str, status: str
    ) -> Dict[str, Any]:
        project_id = self.resolve_project_id(project_id)
        project = self.load_project(project_id)
        if project["status"] != "active":
            raise BridgeError(
                f"Bridge Project {project_id} is archived; reactivate it before updating tasks"
            )
        thread_id = validate_id(thread_id, "bridge thread id")
        if status not in TASK_STATUSES:
            raise BridgeError(f"task status must be one of: {', '.join(sorted(TASK_STATUSES))}")
        current = self.task_states(project_id).get(thread_id)
        if not current:
            raise BridgeError(f"Bridge Thread {thread_id} is not attached to {project_id}")
        if current["status"] == status:
            return current
        self.append_activity(
            project_id,
            "task-status-changed",
            {"bridge_thread_id": thread_id, "status": status},
            dedupe_key=f"task-status:{thread_id}:{status}:{now_iso()}",
        )
        return self.task_states(project_id)[thread_id]

    def project_for_thread(self, thread_id: str) -> str:
        thread_id = validate_id(thread_id, "bridge thread id")
        matches = [
            project_id
            for project_id in self.list_project_ids()
            if thread_id in self.task_states(project_id)
        ]
        if len(matches) > 1:
            raise BridgeError(
                f"Bridge Thread {thread_id} is attached to multiple projects: {matches}"
            )
        return matches[0] if matches else ""

    def compact_context(self, project_id: str = "") -> str:
        project = self.load_project(project_id)
        project_id = project["bridge_project_id"]
        binding = self.load_binding(project_id)
        tasks = self.task_states(project_id)
        manifest = _read_json(self.source_manifest_path(project_id), default={})
        sources = manifest.get("sources", []) if isinstance(manifest, Mapping) else []
        brief = resolve_repo_path(project["brief_path"], self.repo)
        lines = [
            f"- Bridge project id: `{project_id}`",
            f"- Title: {project['title']}",
            f"- Local root label: `{self.repo.name}`",
            f"- Project brief: `{project['brief_path']}`",
            f"- Project brief SHA-256: `{file_sha256(brief)}`",
            f"- Remote binding status: `{binding.get('status', 'unbound') if binding else 'unbound'}`",
            f"- Remote project id: `{binding.get('remote_project_id', '-') if binding else '-'}`",
            f"- Binding last verified: `{binding.get('last_verified_at', '-') if binding else '-'}`",
            f"- Shared source records: {len(sources)}",
            f"- Bridge tasks: {len(tasks)}",
        ]
        if sources:
            lines.extend(["", "## Project Sources", ""])
            for source in sources:
                effective_status = self._effective_source_status(source)
                lines.append(
                    "- `{}` -> `{}` · {} · `{}`".format(
                        source.get("local_path", "-"),
                        source.get("remote_name", "-"),
                        effective_status,
                        source.get("sha256", "-"),
                    )
                )
        if tasks:
            lines.extend(["", "## Bridge Tasks", ""])
            for task in sorted(tasks.values(), key=lambda item: item["updated_at"], reverse=True):
                lines.append(
                    f"- `{task['bridge_thread_id']}` · {task['status']} · {task['title']}"
                )
        return "\n".join(lines) + "\n"

    def verify(self, project_id: str = "") -> Dict[str, Any]:
        project = self.load_project(project_id)
        project_id = project["bridge_project_id"]
        brief = resolve_repo_path(project["brief_path"], self.repo)
        if not brief.is_file():
            raise BridgeError("Project brief is missing")

        events = self.load_activity(project_id)
        if not events or events[0].get("event_type") != "project-created":
            raise BridgeError(
                "Bridge Project activity must begin with project-created"
            )
        expected_parent = ""
        event_ids: set[str] = set()
        dedupe_keys: set[str] = set()
        for index, event in enumerate(events, start=1):
            prefix = f"project event {index}"
            if event.get("schema_version") != PROJECT_SCHEMA_VERSION:
                raise BridgeError(f"{prefix}: unsupported schema version")
            if event.get("event_type") not in PROJECT_EVENT_TYPES:
                raise BridgeError(f"{prefix}: unsupported event type")
            event_id = str(event.get("event_id", ""))
            if not event_id or event_id in event_ids:
                raise BridgeError(f"{prefix}: missing or duplicate event id")
            event_ids.add(event_id)
            if str(event.get("parent_event_id", "")) != expected_parent:
                raise BridgeError(f"{prefix}: parent chain mismatch")
            expected_parent = event_id
            timestamp = str(event.get("occurred_at", ""))
            try:
                parsed = dt.datetime.fromisoformat(timestamp)
            except ValueError as exc:
                raise BridgeError(f"{prefix}: invalid timestamp") from exc
            if parsed.tzinfo is None:
                raise BridgeError(f"{prefix}: timestamp must include a timezone")
            dedupe_key = str(event.get("dedupe_key", ""))
            if dedupe_key:
                if dedupe_key in dedupe_keys:
                    raise BridgeError(f"{prefix}: duplicate dedupe key")
                dedupe_keys.add(dedupe_key)

        binding = self.load_binding(project_id)
        active_binding = bool(binding and binding.get("status") == "active")
        tasks = self.task_states(project_id)
        for thread_id, task in tasks.items():
            if task["status"] not in TASK_STATUSES:
                raise BridgeError(
                    f"Bridge Task {thread_id} has invalid status {task['status']!r}"
                )
            missing = [dependency for dependency in task["depends_on"] if dependency not in tasks]
            if missing:
                raise BridgeError(
                    f"Bridge Task {thread_id} has unknown dependencies: {missing}"
                )
            event_projects = {
                str(event.get("bridge_project_id"))
                for event in load_events(self.bridge_dir, thread_id)
                if event.get("bridge_project_id")
            }
            if event_projects and event_projects != {project_id}:
                raise BridgeError(
                    f"Bridge Thread {thread_id} has conflicting project ids: {event_projects}"
                )

        visiting: set[str] = set()
        visited: set[str] = set()

        def visit(thread_id: str) -> None:
            if thread_id in visiting:
                raise BridgeError(
                    f"Bridge Task dependency cycle includes {thread_id}"
                )
            if thread_id in visited:
                return
            visiting.add(thread_id)
            for dependency in tasks[thread_id]["depends_on"]:
                visit(dependency)
            visiting.remove(thread_id)
            visited.add(thread_id)

        for thread_id in tasks:
            visit(thread_id)

        manifest = _read_json(self.source_manifest_path(project_id), default={})
        if manifest and (
            manifest.get("schema_version") != PROJECT_SCHEMA_VERSION
            or manifest.get("bridge_project_id") != project_id
        ):
            raise BridgeError("Project source manifest identity is invalid")
        if (
            manifest
            and binding
            and manifest.get("remote_project_id")
            != binding.get("remote_project_id")
        ):
            raise BridgeError(
                "Project source manifest targets a different ChatGPT Project binding"
            )
        source_status_counts: Dict[str, int] = {}
        for source in manifest.get("sources", []) if manifest else []:
            stored_status = str(source.get("sync_status", ""))
            if stored_status not in SOURCE_SYNC_STATUSES:
                raise BridgeError(
                    f"Project source manifest has invalid sync status: {stored_status!r}"
                )
            status = self._effective_source_status(source)
            source_status_counts[status] = source_status_counts.get(status, 0) + 1
        source_count = len(manifest.get("sources", [])) if manifest else 0
        last_inventory_checked_at = (
            str(manifest.get("last_inventory_checked_at", ""))
            if manifest
            else ""
        )
        last_inventory_verified_at = (
            str(manifest.get("last_inventory_verified_at", ""))
            if manifest
            else ""
        )
        inventory_verified = bool(
            not source_count
            or (
                last_inventory_checked_at
                and last_inventory_verified_at == last_inventory_checked_at
            )
        )
        return {
            "valid": True,
            "bridge_project_id": project_id,
            "project_status": project["status"],
            "active_binding": active_binding,
            "binding_status": binding.get("status", "unbound") if binding else "unbound",
            "project_event_count": len(events),
            "task_count": len(tasks),
            "source_count": source_count,
            "source_status_counts": source_status_counts,
            "unsynced_source_count": sum(
                count
                for status, count in source_status_counts.items()
                if status != "synced"
            ),
            "inventory_verified": inventory_verified,
            "last_inventory_checked_at": last_inventory_checked_at,
            "last_inventory_verified_at": last_inventory_verified_at,
            "brief_sha256": file_sha256(brief),
        }

    def _effective_source_status(self, source: Mapping[str, Any]) -> str:
        status = str(source.get("sync_status", ""))
        local_path = str(source.get("local_path", ""))
        if not local_path:
            return "missing"
        path = resolve_repo_path(local_path, self.repo, must_exist=False)
        if not path.is_file():
            return "missing"
        expected_sha = str(source.get("sha256", ""))
        if expected_sha and file_sha256(path) != expected_sha:
            return "stale"
        return status

    def _validate_project(
        self, project: Mapping[str, Any], *, expected_id: str
    ) -> None:
        if not project:
            raise BridgeError(f"Missing project metadata for {expected_id}")
        if project.get("schema_version") != PROJECT_SCHEMA_VERSION:
            raise BridgeError(f"Unsupported Bridge Project schema for {expected_id}")
        if project.get("bridge_project_id") != expected_id:
            raise BridgeError(f"Bridge Project id mismatch for {expected_id}")
        if Path(str(project.get("local_root", ""))).resolve() != self.repo:
            raise BridgeError(
                "Bridge Project local root no longer matches this repository; "
                "repair the local binding explicitly"
            )
        if project.get("status") not in {"active", "archived"}:
            raise BridgeError(f"Invalid Bridge Project status for {expected_id}")

    def _validate_binding(
        self, binding: Mapping[str, Any], *, expected_project_id: str
    ) -> None:
        if binding.get("schema_version") != PROJECT_SCHEMA_VERSION:
            raise BridgeError("Unsupported ChatGPT Project binding schema")
        if binding.get("bridge_project_id") != expected_project_id:
            raise BridgeError("ChatGPT Project binding belongs to another Bridge Project")
        if binding.get("provider") != "chatgpt-web":
            raise BridgeError("Unsupported project binding provider")
        if binding.get("status") not in BINDING_STATUSES:
            raise BridgeError("Invalid ChatGPT Project binding status")
        if binding.get("sync_mode") not in SYNC_MODES:
            raise BridgeError("Invalid ChatGPT Project sync mode")
        if binding.get("status") == "active" and (
            not str(binding.get("workspace", "")).strip()
            or not str(binding.get("account_label", "")).strip()
            or not str(binding.get("last_verified_at", "")).strip()
        ):
            raise BridgeError(
                "An active ChatGPT Project binding must retain verified account identity"
            )
        remote_id = str(binding.get("remote_project_id", ""))
        if remote_id and not REMOTE_PROJECT_ID_RE.fullmatch(remote_id):
            raise BridgeError("Invalid ChatGPT Project id in binding")
        if binding.get("remote_project_url"):
            normalize_remote_project_url(str(binding["remote_project_url"]))

    def _render_overview(self, project_id: str) -> None:
        project_path = self.project_dir(project_id) / "project.json"
        if not project_path.is_file():
            return
        project = _read_json(project_path, default={})
        binding = _read_json(
            self.project_dir(project_id) / "remote-binding.json", default={}
        )
        manifest = _read_json(self.source_manifest_path(project_id), default={})
        tasks = self.task_states(project_id)
        lines = [
            f"# Bridge Project: {project.get('title', project_id)}",
            "",
            "## Identity",
            "",
            f"- Bridge Project ID: `{project_id}`",
            f"- Local root: `{project.get('local_root', '-')}`",
            f"- Project brief: `{project.get('brief_path', '-')}`",
            f"- Status: `{project.get('status', '-')}`",
            "",
            "## ChatGPT Project Binding",
            "",
            f"- Status: `{binding.get('status', 'unbound') if binding else 'unbound'}`",
            f"- Remote Project ID: `{binding.get('remote_project_id', '-') if binding else '-'}`",
            f"- Remote URL: {binding.get('remote_project_url', '-') if binding else '-'}",
            f"- Expected workspace: `{binding.get('workspace', '-') if binding else '-'}`",
            f"- Expected account: `{binding.get('account_label', '-') if binding else '-'}`",
            f"- Last observed workspace: `{binding.get('last_observed_workspace', '-') if binding else '-'}`",
            f"- Last observed account: `{binding.get('last_observed_account_label', '-') if binding else '-'}`",
            f"- Last checked: `{binding.get('last_checked_at', '-') if binding else '-'}`",
            f"- Last verified: `{binding.get('last_verified_at', '-') if binding else '-'}`",
            f"- Sync mode: `{binding.get('sync_mode', '-') if binding else '-'}`",
            "",
            "## Shared Sources",
            "",
            "| Local Source | Remote Name | Ownership | Sync Status | SHA-256 |",
            "| --- | --- | --- | --- | --- |",
        ]
        sources = manifest.get("sources", []) if isinstance(manifest, Mapping) else []
        if sources:
            for source in sources:
                lines.append(
                    "| `{}` | `{}` | `{}` | `{}` | `{}` |".format(
                        source.get("local_path", "-"),
                        source.get("remote_name", "-"),
                        source.get("ownership", "-"),
                        source.get("sync_status", "-"),
                        source.get("sha256", "-"),
                    )
                )
        else:
            lines.append("| _None_ | - | - | - | - |")
        lines.extend(
            [
                "",
                "## Bridge Tasks",
                "",
                "| Bridge Thread | Status | Title | Dependencies |",
                "| --- | --- | --- | --- |",
            ]
        )
        if tasks:
            for task in sorted(tasks.values(), key=lambda item: item["updated_at"], reverse=True):
                dependencies = ", ".join(task["depends_on"]) or "-"
                lines.append(
                    f"| `{task['bridge_thread_id']}` | `{task['status']}` | "
                    f"{task['title'].replace('|', '\\|')} | {dependencies} |"
                )
        else:
            lines.append("| _None_ | - | - | - |")
        atomic_write_text(
            self.project_dir(project_id) / "overview.md", "\n".join(lines) + "\n"
        )

    def _write_index(self) -> None:
        self.projects_dir.mkdir(parents=True, exist_ok=True)
        lines = [
            "# Codex Pro Bridge Projects",
            "",
            "| Bridge Project | Title | Local Root | Remote Project | Binding | Tasks | Open |",
            "| --- | --- | --- | --- | --- | --- | --- |",
        ]
        for project_id in self.list_project_ids():
            project = _read_json(self.project_dir(project_id) / "project.json", default={})
            binding = _read_json(
                self.project_dir(project_id) / "remote-binding.json", default={}
            )
            task_count = len(self.task_states(project_id))
            lines.append(
                "| `{}` | {} | `{}` | `{}` | `{}` | {} | [open]({}/overview.md) |".format(
                    project_id,
                    str(project.get("title", project_id)).replace("|", "\\|"),
                    project.get("local_root", "-"),
                    binding.get("remote_project_id", "-") if binding else "-",
                    binding.get("status", "unbound") if binding else "unbound",
                    task_count,
                    project_id,
                )
            )
        atomic_write_text(self.projects_dir / "index.md", "\n".join(lines) + "\n")
