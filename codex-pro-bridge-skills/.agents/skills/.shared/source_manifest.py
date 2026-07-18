#!/usr/bin/env python3
"""Plan and record safe ChatGPT Project Source synchronization."""

from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path
from typing import Any, Dict, List, Mapping, Sequence

from bridge_store import (
    BridgeError,
    atomic_write_text,
    file_sha256,
    now_iso,
    repo_relative,
    resolve_repo_path,
    unique_artifact_path,
)
from evidence_safety import is_excluded_by_name, scan_for_secrets
from project_store import BridgeProjectStore, PROJECT_SCHEMA_VERSION


SOURCE_ROLES = {"brief", "decisions", "glossary", "literature", "experiment", "reference"}
SOURCE_OWNERSHIP = {"bridge_managed", "user_managed"}
DEFAULT_MAX_SOURCE_BYTES = 512 * 1024 * 1024


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


def _source_id(relative_path: str) -> str:
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()[:16]


def _slug(value: str, fallback: str = "source") -> str:
    slug = re.sub(r"[^a-z0-9]+", "-", value.casefold()).strip("-")
    return slug or fallback


def _infer_role(path: Path) -> str:
    name = path.stem.casefold().replace("-", "_")
    if "project_brief" in name or name == "brief":
        return "brief"
    if "decision" in name or "adr" in name:
        return "decisions"
    if "glossary" in name or "context" == name:
        return "glossary"
    if "literature" in name or "paper" in name:
        return "literature"
    if "experiment" in name or "result" in name:
        return "experiment"
    return "reference"


def _normalize_remote_inventory(
    value: Sequence[Any] | Mapping[str, Any] | None,
) -> List[Dict[str, Any]]:
    if value is None:
        return []
    raw_items = value.get("files", []) if isinstance(value, Mapping) else value
    if not isinstance(raw_items, Sequence) or isinstance(raw_items, (str, bytes)):
        raise BridgeError("Remote inventory must be a list or an object with a files list")
    result: List[Dict[str, Any]] = []
    seen: set[str] = set()
    for item in raw_items:
        if isinstance(item, str):
            entry: Dict[str, Any] = {"name": item}
        elif isinstance(item, Mapping):
            entry = dict(item)
        else:
            raise BridgeError("Every remote inventory item must be a string or object")
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        if name in seen:
            raise BridgeError(f"Remote inventory contains duplicate filename: {name}")
        seen.add(name)
        raw_ownership = str(entry.get("ownership", "")).strip()
        ownership = raw_ownership or "user_managed"
        if ownership not in SOURCE_OWNERSHIP:
            raise BridgeError(f"Invalid source ownership for {name}: {ownership}")
        try:
            size_bytes = int(entry.get("size_bytes", 0) or 0)
        except (TypeError, ValueError) as exc:
            raise BridgeError(f"Invalid source size for {name}") from exc
        if size_bytes < 0:
            raise BridgeError(f"Source size cannot be negative for {name}")
        result.append(
            {
                "name": name,
                "ownership": ownership,
                "ownership_observed": bool(raw_ownership),
                "size_bytes": size_bytes,
            }
        )
    return result


class ProjectSourceManager:
    """Compute source effects before the browser adapter mutates ChatGPT."""

    def __init__(self, repo: Path):
        self.repo = repo.resolve()
        self.store = BridgeProjectStore(self.repo)

    def load_manifest(self, project_id: str = "") -> Dict[str, Any]:
        project_id = self.store.resolve_project_id(project_id)
        path = self.store.source_manifest_path(project_id)
        manifest = _read_json(path, default={})
        if manifest and (
            manifest.get("schema_version") != PROJECT_SCHEMA_VERSION
            or manifest.get("bridge_project_id") != project_id
        ):
            raise BridgeError("Project source manifest identity is invalid")
        return manifest

    def reconcile_remote_inventory(
        self,
        project_id: str,
        *,
        remote_inventory: Sequence[Any] | Mapping[str, Any],
    ) -> Dict[str, Any]:
        project = self.store.load_project(project_id)
        project_id = project["bridge_project_id"]
        binding = self.store.load_binding(project_id)
        if not binding or binding.get("status") != "active":
            raise BridgeError(
                "Remote inventory reconciliation requires an active "
                "ChatGPT Project binding"
            )
        manifest = self.load_manifest(project_id)
        if not manifest:
            raise BridgeError("Project source manifest does not exist")
        if manifest.get("remote_project_id") != binding.get("remote_project_id"):
            raise BridgeError(
                "Project source manifest targets a different ChatGPT Project binding"
            )

        normalized_inventory = _normalize_remote_inventory(remote_inventory)
        inventory_by_name = {
            str(item["name"]): item for item in normalized_inventory
        }
        expected_names = {
            str(source.get("remote_name", ""))
            for source in manifest.get("sources", [])
            if source.get("remote_name")
        }
        missing_remote = sorted(expected_names - set(inventory_by_name))
        ownership_mismatches = sorted(
            name
            for name in expected_names & set(inventory_by_name)
            if inventory_by_name[name].get("ownership") != "bridge_managed"
        )

        now = now_iso()
        status_counts: Dict[str, int] = {}
        for source in manifest.get("sources", []):
            remote_name = str(source.get("remote_name", ""))
            if remote_name in missing_remote:
                status = "missing"
            elif remote_name in ownership_mismatches:
                status = "failed"
            else:
                source["sync_status"] = "synced"
                status = self.store._effective_source_status(source)
            source["sync_status"] = status
            source["last_observed_at"] = now
            if status == "synced":
                source["last_synced_at"] = now
            status_counts[status] = status_counts.get(status, 0) + 1

        valid = (
            not missing_remote
            and not ownership_mismatches
            and all(status == "synced" for status in status_counts)
        )
        manifest["remote_inventory"] = normalized_inventory
        manifest["last_inventory_checked_at"] = now
        if valid:
            manifest["last_inventory_verified_at"] = now
        manifest["updated_at"] = now
        _write_json(self.store.source_manifest_path(project_id), manifest)
        self.store.append_activity(
            project_id,
            "source-inventory-checked",
            {
                "remote_project_id": binding["remote_project_id"],
                "valid": valid,
                "missing_remote": missing_remote,
                "ownership_mismatches": ownership_mismatches,
                "source_status_counts": status_counts,
            },
            dedupe_key=(
                f"source-inventory-checked:{binding['remote_project_id']}:{now}"
            ),
        )
        return {
            "valid": valid,
            "bridge_project_id": project_id,
            "remote_project_id": binding["remote_project_id"],
            "observed_at": now,
            "source_count": len(manifest.get("sources", [])),
            "remote_inventory_count": len(normalized_inventory),
            "missing_remote": missing_remote,
            "ownership_mismatches": ownership_mismatches,
            "source_status_counts": status_counts,
        }

    def plan(
        self,
        project_id: str,
        *,
        source_paths: Sequence[str] = (),
        source_roles: Mapping[str, str] | None = None,
        remote_inventory: Sequence[Any] | Mapping[str, Any] | None = None,
        max_project_files: int = 0,
        max_source_bytes: int = DEFAULT_MAX_SOURCE_BYTES,
        allow_secret_like_content: bool = False,
    ) -> tuple[Dict[str, Any], Path]:
        project = self.store.load_project(project_id)
        project_id = project["bridge_project_id"]
        if project["status"] != "active":
            raise BridgeError(
                f"Bridge Project {project_id} is archived; reactivate it before syncing sources"
            )
        binding = self.store.load_binding(project_id)
        if not binding or binding.get("status") != "active":
            raise BridgeError("Project Source planning requires an active ChatGPT Project binding")
        if binding.get("sync_mode") == "read_only":
            raise BridgeError("The active ChatGPT Project binding is read-only")
        if max_project_files < 0 or max_source_bytes <= 0:
            raise BridgeError(
                "Project file capacity must be non-negative and source size must be positive"
            )
        capacity = max_project_files or int(binding.get("max_project_files", 0) or 0)
        roles = dict(source_roles or {})
        existing_manifest = self.load_manifest(project_id)
        existing_sources = {
            str(item.get("source_id", "")): dict(item)
            for item in existing_manifest.get("sources", [])
            if isinstance(item, Mapping) and item.get("source_id")
        }

        desired_values = [project["brief_path"]]
        desired_values.extend(
            str(item.get("local_path"))
            for item in existing_sources.values()
            if item.get("local_path")
        )
        desired_values.extend(source_paths)
        desired_values = list(dict.fromkeys(value for value in desired_values if value))

        project_dir = self.store.project_dir(project_id)
        default_internal_brief = (project_dir / "PROJECT_BRIEF.md").resolve()
        desired: List[Dict[str, Any]] = []
        for value in desired_values:
            path = resolve_repo_path(value, self.repo)
            if not path.is_file():
                raise BridgeError(f"Project Source must be a file: {value}")
            if path.stat().st_size > max_source_bytes:
                raise BridgeError(f"Project Source exceeds the configured size limit: {value}")
            if (
                path.resolve() != default_internal_brief
                and is_excluded_by_name(path, self.repo)
            ):
                raise BridgeError(f"Project Source is excluded by the safety policy: {value}")
            flagged = scan_for_secrets([path], max_source_bytes)
            if flagged and not allow_secret_like_content:
                raise BridgeError(
                    f"High-confidence secret-like content found in Project Source: {value}"
                )
            relative = repo_relative(path, self.repo)
            source_id = _source_id(relative)
            previous = existing_sources.get(source_id, {})
            role = roles.get(
                relative,
                roles.get(value, str(previous.get("role", "")) or _infer_role(path)),
            )
            if role not in SOURCE_ROLES:
                raise BridgeError(
                    f"Source role for {relative} must be one of: "
                    + ", ".join(sorted(SOURCE_ROLES))
                )
            sha = file_sha256(path)
            suffix = path.suffix.lower() or ".txt"
            remote_name = f"bridge--{role}-{_slug(path.stem)}--{sha[:12]}{suffix}"
            desired.append(
                {
                    "source_id": source_id,
                    "role": role,
                    "local_path": relative,
                    "remote_name": remote_name,
                    "sha256": sha,
                    "size_bytes": path.stat().st_size,
                    "ownership": "bridge_managed",
                    "previous_remote_name": previous.get("remote_name", ""),
                }
            )

        inventory = _normalize_remote_inventory(remote_inventory)
        known_bridge_names = {
            str(item.get("remote_name", ""))
            for item in existing_sources.values()
            if item.get("remote_name")
        }
        for item in inventory:
            if (
                item["name"] in known_bridge_names
                and not item["ownership_observed"]
            ):
                item["ownership"] = "bridge_managed"
        inventory_by_name = {item["name"]: item for item in inventory}
        uploads: List[Dict[str, Any]] = []
        reused: List[Dict[str, Any]] = []
        removals: List[Dict[str, Any]] = []
        upload_names: set[str] = set()
        removal_names: set[str] = set()
        for source in desired:
            if source["remote_name"] in inventory_by_name:
                reused.append(source)
            elif source["remote_name"] not in upload_names:
                uploads.append(source)
                upload_names.add(source["remote_name"])
            previous_name = str(source.get("previous_remote_name", ""))
            if (
                binding.get("sync_mode") == "managed"
                and previous_name
                and previous_name != source["remote_name"]
                and previous_name in inventory_by_name
                and inventory_by_name[previous_name]["ownership"] == "bridge_managed"
                and previous_name not in removal_names
            ):
                removals.append(
                    {
                        "name": previous_name,
                        "source_id": source["source_id"],
                        "ownership": "bridge_managed",
                        "timing": "after-upload-verification",
                    }
                )
                removal_names.add(previous_name)

        remote_count = len(inventory)
        temporary_peak = remote_count + len(uploads)
        final_count = temporary_peak - len(removals)
        blockers: List[str] = []
        if capacity and temporary_peak > capacity:
            operation = (
                "Managed two-phase replacement" if removals else "Project Source upload"
            )
            blockers.append(
                f"{operation} needs {temporary_peak} remote slots, above capacity {capacity}"
            )
        plan_path = unique_artifact_path(
            self.store.source_plans_dir(project_id), "project-source-sync", ".json"
        )
        staged_uploads: List[Dict[str, Any]] = []
        staging_dir = self.store.sources_dir(project_id) / "staging" / plan_path.stem
        if not blockers:
            staging_dir.mkdir(parents=True, exist_ok=False)
            for source in uploads:
                source_path = resolve_repo_path(str(source["local_path"]), self.repo)
                upload_path = staging_dir / str(source["remote_name"])
                shutil.copyfile(source_path, upload_path)
                if file_sha256(upload_path) != source["sha256"]:
                    raise BridgeError(
                        f"Staged Project Source hash mismatch: {source['local_path']}"
                    )
                staged = dict(source)
                staged["upload_path"] = str(upload_path.resolve())
                staged_uploads.append(staged)

        plan = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "bridge_project_id": project_id,
            "remote_project_id": binding["remote_project_id"],
            "sync_mode": binding["sync_mode"],
            "created_at": now_iso(),
            "capacity": capacity,
            "remote_count_before": remote_count,
            "temporary_peak": temporary_peak,
            "remote_count_after": final_count,
            "ready": not blockers,
            "blockers": blockers,
            "desired_sources": desired,
            "uploads": staged_uploads if not blockers else uploads,
            "reuse": reused,
            "removals_after_upload": removals,
            "remote_inventory": inventory,
            "staging_dir": str(staging_dir.resolve()) if not blockers else "",
        }
        _write_json(plan_path, plan)
        return plan, plan_path

    def record(
        self,
        project_id: str,
        *,
        plan_path: Path,
        observation: Mapping[str, Any],
    ) -> Dict[str, Any]:
        project = self.store.load_project(project_id)
        project_id = project["bridge_project_id"]
        plan_path = plan_path.resolve()
        expected_root = self.store.source_plans_dir(project_id).resolve()
        try:
            plan_path.relative_to(expected_root)
        except ValueError as exc:
            raise BridgeError("Source sync plan must belong to the Bridge Project") from exc
        plan = _read_json(plan_path, default={})
        if (
            plan.get("schema_version") != PROJECT_SCHEMA_VERSION
            or plan.get("bridge_project_id") != project_id
        ):
            raise BridgeError("Source sync plan identity is invalid")
        if not plan.get("ready"):
            raise BridgeError("Cannot record a blocked Project Source sync plan")
        binding = self.store.load_binding(project_id)
        if not binding or binding.get("status") != "active":
            raise BridgeError(
                "Recording Project Sources requires an active ChatGPT Project binding"
            )
        if plan.get("remote_project_id") != binding.get("remote_project_id"):
            raise BridgeError(
                "The Project Source plan targets an obsolete ChatGPT Project binding; "
                "inventory the current Project and plan again"
            )
        if plan.get("sync_mode") != binding.get("sync_mode"):
            raise BridgeError(
                "The Project Source sync mode changed after planning; plan again"
            )

        present = {str(item) for item in observation.get("present", [])}
        removed = {str(item) for item in observation.get("removed", [])}
        failed = {
            str(item.get("name", ""))
            if isinstance(item, Mapping)
            else str(item)
            for item in observation.get("failed", [])
            if item
        }
        failed.discard("")
        expected_names = {
            str(item["remote_name"]) for item in plan.get("desired_sources", [])
        }
        remote_inventory = _normalize_remote_inventory(
            observation.get("remote_inventory", plan.get("remote_inventory", []))
        )
        inventory_by_name = {
            str(item["name"]): item for item in remote_inventory
        }
        inventory_names = {str(item["name"]) for item in remote_inventory}
        unexpected_present = present - expected_names
        if unexpected_present:
            raise BridgeError(
                "Source observation contains files outside the plan: "
                + ", ".join(sorted(unexpected_present))
            )
        unconfirmed_present = present - inventory_names
        if unconfirmed_present:
            raise BridgeError(
                "Source observation claimed present but absent from remote inventory: "
                + ", ".join(sorted(unconfirmed_present))
            )
        misowned_present = {
            name
            for name in present
            if inventory_by_name[name].get("ownership") != "bridge_managed"
        }
        if misowned_present:
            raise BridgeError(
                "Source observation claimed present but files were not observed as "
                "bridge-managed: "
                + ", ".join(sorted(misowned_present))
            )
        unexpected_failed = failed - expected_names
        if unexpected_failed:
            raise BridgeError(
                "Source observation contains failures outside the plan: "
                + ", ".join(sorted(unexpected_failed))
            )
        conflicting_results = present & failed
        if conflicting_results:
            raise BridgeError(
                "Source observation marks files both present and failed: "
                + ", ".join(sorted(conflicting_results))
            )
        planned_removals = {
            str(item["name"]) for item in plan.get("removals_after_upload", [])
        }
        if removed - planned_removals:
            raise BridgeError(
                "Source observation removed files outside the plan: "
                + ", ".join(sorted(removed - planned_removals))
            )
        still_present = removed & inventory_names
        if still_present:
            raise BridgeError(
                "Source observation claimed removed but files remain in remote inventory: "
                + ", ".join(sorted(still_present))
            )
        if any(
            item.get("ownership") != "bridge_managed"
            for item in plan.get("removals_after_upload", [])
        ):
            raise BridgeError("A Project Source plan may never remove user-managed files")

        now = now_iso()
        sources: List[Dict[str, Any]] = []
        for desired in plan.get("desired_sources", []):
            remote_name = str(desired["remote_name"])
            status = (
                "failed"
                if remote_name in failed
                else "synced"
                if remote_name in present
                else "pending"
            )
            sources.append(
                {
                    "source_id": desired["source_id"],
                    "role": desired["role"],
                    "local_path": desired["local_path"],
                    "remote_name": remote_name,
                    "sha256": desired["sha256"],
                    "size_bytes": desired["size_bytes"],
                    "ownership": "bridge_managed",
                    "sync_status": status,
                    "last_observed_at": now,
                    "last_synced_at": now if status == "synced" else "",
                }
            )
        manifest = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "bridge_project_id": project_id,
            "remote_project_id": plan["remote_project_id"],
            "updated_at": now,
            "last_inventory_checked_at": now,
            "last_plan": repo_relative(plan_path, self.repo),
            "last_plan_sha256": file_sha256(plan_path),
            "sources": sources,
            "remote_inventory": remote_inventory,
        }
        if all(source["sync_status"] == "synced" for source in sources):
            manifest["last_inventory_verified_at"] = now
        _write_json(
            self.store.source_manifest_path(project_id), manifest
        )
        self.store.append_activity(
            project_id,
            "source-synced",
            {
                "plan": manifest["last_plan"],
                "plan_sha256": manifest["last_plan_sha256"],
                "synced": sum(item["sync_status"] == "synced" for item in sources),
                "pending": sum(item["sync_status"] == "pending" for item in sources),
                "failed": sum(item["sync_status"] == "failed" for item in sources),
                "removed": sorted(removed),
            },
            dedupe_key=f"source-synced:{manifest['last_plan_sha256']}:{hashlib.sha256(json.dumps(observation, sort_keys=True).encode()).hexdigest()}",
        )
        return manifest
