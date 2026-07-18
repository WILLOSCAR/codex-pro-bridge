#!/usr/bin/env python3
"""Shared persistence and rendering for Codex Pro Bridge.

The JSONL event ledger is canonical. Markdown timelines and indexes are derived
views and can be regenerated at any time.
"""

from __future__ import annotations

import contextlib
import datetime as dt
import fcntl
import hashlib
import json
import os
import re
import tempfile
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Iterator, List, Mapping, Sequence


SCHEMA_VERSION = 1
SEQUENCE_EVENT_LIMIT = 40
ID_RE = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")
LEGACY_EVENT_MAP = {
    "codex-update": "codex-snapshot",
    "bundle": "legacy-bundle",
    "gpt-pro-turn": "gpt-exchange",
}


class BridgeError(ValueError):
    """Raised when bridge state would become ambiguous or unsafe."""


def now_iso() -> str:
    return dt.datetime.now().astimezone().isoformat(timespec="seconds")


def timestamp_slug() -> str:
    return dt.datetime.now().astimezone().strftime("%Y%m%d-%H%M%S-%f")


def validate_id(value: str, field: str) -> str:
    value = (value or "").strip()
    if not value:
        raise BridgeError(f"{field} is required")
    if len(value) > 80 or not ID_RE.fullmatch(value):
        raise BridgeError(
            f"{field} must be 1-80 lowercase letters, digits, or hyphens, "
            "and must start and end with a letter or digit"
        )
    return value


def default_codex_session_id(thread_id: str) -> str:
    return _derived_session_id(thread_id, "-codex", "Codex session id")


def default_gpt_session_id(thread_id: str) -> str:
    return _derived_session_id(thread_id, "-gpt-pro", "GPT Pro session id")


def _derived_session_id(thread_id: str, suffix: str, field: str) -> str:
    thread_id = validate_id(thread_id, "bridge thread id")
    raw = f"{thread_id}{suffix}"
    if len(raw) <= 80:
        return validate_id(raw, field)
    fingerprint = hashlib.sha256(thread_id.encode("utf-8")).hexdigest()[:8]
    keep = 80 - len(suffix) - len(fingerprint) - 1
    shortened = thread_id[:keep].rstrip("-")
    return validate_id(f"{shortened}-{fingerprint}{suffix}", field)


def bridge_root(repo: Path) -> Path:
    return repo.resolve() / ".codex" / "codex-pro-bridge"


def is_within(path: Path, root: Path) -> bool:
    try:
        path.resolve().relative_to(root.resolve())
        return True
    except ValueError:
        return False


def repo_relative(path: Path, repo: Path) -> str:
    try:
        return path.resolve().relative_to(repo.resolve()).as_posix()
    except ValueError as exc:
        raise BridgeError(f"Path is outside repository root: {path}") from exc


def resolve_repo_path(value: str, repo: Path, *, must_exist: bool = True) -> Path:
    path = Path(value)
    path = path.resolve() if path.is_absolute() else (repo / path).resolve()
    if not is_within(path, repo):
        raise BridgeError(f"Path is outside repository root: {value}")
    if must_exist and not path.exists():
        raise BridgeError(f"Path does not exist: {value}")
    return path


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_quote(value: Any) -> str:
    return json.dumps(value if value is not None else "", ensure_ascii=False)


def parse_metadata(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    result: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# ") or line.startswith("## "):
            break
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        raw = raw.strip()
        if not key:
            continue
        try:
            result[key] = str(json.loads(raw))
        except Exception:
            result[key] = raw.strip("'\"")
    return result


def atomic_write_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temp_name = tempfile.mkstemp(prefix=f".{path.name}.", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temp_name, path)
    except Exception:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(temp_name)
        raise


@contextlib.contextmanager
def file_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+", encoding="utf-8") as handle:
        fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


def write_bound_metadata(
    path: Path,
    values: Mapping[str, Any],
    *,
    ordered_keys: Sequence[str],
    immutable_keys: Sequence[str],
) -> Dict[str, str]:
    """Write metadata while rejecting identity changes."""
    lock_path = path.with_name(f".{path.name}.lock")
    with file_lock(lock_path):
        previous = parse_metadata(path)
        merged: Dict[str, str] = dict(previous)
        for key, raw_value in values.items():
            value = str(raw_value or "")
            old = previous.get(key, "")
            if key in immutable_keys and old and value and old != value:
                raise BridgeError(f"Cannot change {key} from {old!r} to {value!r} in {path}")
            if value or key not in merged:
                merged[key] = value
        lines = [f"{key}: {json_quote(merged.get(key, ''))}" for key in ordered_keys]
        atomic_write_text(path, "\n".join(lines) + "\n")
        return merged


def unique_artifact_path(directory: Path, stem: str, suffix: str) -> Path:
    directory.mkdir(parents=True, exist_ok=True)
    safe_stem = re.sub(r"[^a-zA-Z0-9._-]+", "-", stem).strip("-") or "artifact"
    for _ in range(10):
        candidate = directory / f"{timestamp_slug()}-{safe_stem}-{uuid.uuid4().hex[:8]}{suffix}"
        if not candidate.exists():
            return candidate
    raise BridgeError(f"Could not allocate a unique artifact path under {directory}")


def require_new_output(path: Path) -> None:
    if path.exists():
        raise BridgeError(f"Refusing to overwrite immutable artifact: {path}")


def _legacy_events(markdown_path: Path, thread_id: str) -> List[Dict[str, Any]]:
    if not markdown_path.exists():
        return []
    text = markdown_path.read_text(encoding="utf-8")
    marker = "## Timeline\n"
    if marker not in text:
        return []
    timeline = text.split(marker, 1)[1]
    meta = parse_metadata(markdown_path)
    raw_events: List[tuple[str, str, List[str]]] = []
    current: tuple[str, str, List[str]] | None = None
    for line in timeline.splitlines():
        if line.startswith("### "):
            if current:
                raw_events.append(current)
            header = line[4:].strip()
            if " - " in header:
                occurred_at, event_type = header.split(" - ", 1)
            else:
                occurred_at, event_type = "", header
            current = (occurred_at, event_type, [])
        elif current is not None:
            current[2].append(line)
    if current:
        raw_events.append(current)

    events: List[Dict[str, Any]] = []
    parent = ""
    for index, (occurred_at, legacy_type, lines) in enumerate(raw_events, start=1):
        fingerprint = hashlib.sha256(
            (occurred_at + legacy_type + "\n".join(lines)).encode("utf-8")
        ).hexdigest()[:10]
        event_id = f"legacy-{index:04d}-{fingerprint}"
        mapped = LEGACY_EVENT_MAP.get(legacy_type, f"legacy-{legacy_type}")
        details = "\n".join(lines)
        codex_match = re.search(r"Codex session:\s*`([^`]+)`", details)
        gpt_match = re.search(r"GPT Pro session:\s*`([^`]+)`", details)
        codex_session_id = codex_match.group(1) if codex_match else ""
        gpt_session_id = gpt_match.group(1) if gpt_match else ""
        if not codex_session_id:
            codex_session_id = (meta.get("codex_session_ids", "").split(",", 1)[0]).strip()
        if not gpt_session_id:
            gpt_session_id = (meta.get("gpt_pro_session_ids", "").split(",", 1)[0]).strip()
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": event_id,
            "thread_id": thread_id,
            "event_type": mapped,
            "occurred_at": occurred_at or meta.get("created_at", ""),
            "actor": "gpt-pro" if mapped == "gpt-exchange" else "codex",
            "parent_event_id": parent,
            "thread_title": meta.get("title", thread_id),
            "codex_session_id": codex_session_id,
            "gpt_pro_session_id": gpt_session_id,
            "artifact": {},
            "data": {"legacy_event_type": legacy_type, "legacy_details": lines},
        }
        events.append(event)
        parent = event_id
    return events


def _jsonl_path(bridge_dir: Path, thread_id: str) -> Path:
    return bridge_dir / "threads" / f"{thread_id}.jsonl"


def _markdown_path(bridge_dir: Path, thread_id: str) -> Path:
    return bridge_dir / "threads" / f"{thread_id}.md"


def load_events(bridge_dir: Path, thread_id: str) -> List[Dict[str, Any]]:
    thread_id = validate_id(thread_id, "bridge thread id")
    jsonl_path = _jsonl_path(bridge_dir, thread_id)
    if not jsonl_path.exists():
        return _legacy_events(_markdown_path(bridge_dir, thread_id), thread_id)
    events: List[Dict[str, Any]] = []
    for line_number, line in enumerate(jsonl_path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError as exc:
            raise BridgeError(f"Invalid JSONL at {jsonl_path}:{line_number}: {exc}") from exc
        if event.get("thread_id") != thread_id:
            raise BridgeError(f"Thread id mismatch in {jsonl_path}:{line_number}")
        events.append(event)
    return events


def _event_summary(event: Mapping[str, Any]) -> str:
    data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
    for key in ("summary", "question", "goal", "verification", "turn"):
        value = str(data.get(key, "")).strip()
        if value:
            return re.sub(r"\s+", " ", value)[:90]
    return event.get("event_type", "event")


def _sequence_diagram(events: Sequence[Mapping[str, Any]]) -> str:
    selected = list(events[-SEQUENCE_EVENT_LIMIT:])
    start = len(events) - len(selected) + 1
    lines = [
        "```mermaid",
        "sequenceDiagram",
        "  participant C as Codex",
        "  participant G as GPT Pro",
    ]
    for offset, event in enumerate(selected):
        number = start + offset
        event_type = str(event.get("event_type", "event"))
        label = re.sub(r"[\r\n:]+", " ", _event_summary(event)).replace('"', "'")
        label = f"{number:02d} {event_type}: {label}"[:150]
        if event_type == "gpt-exchange":
            lines.append(f"  C->>G: {label}")
            lines.append(f"  G-->>C: {number:02d} response captured")
        else:
            lines.append(f"  C->>C: {label}")
    lines.append("```")
    if len(events) > len(selected):
        return f"_View shows the latest {len(selected)} of {len(events)} events._\n\n" + "\n".join(lines)
    return "\n".join(lines)


def _format_value(value: Any) -> str:
    if isinstance(value, Mapping):
        parts = [f"{key}={item}" for key, item in value.items() if item not in (None, "", [], {})]
        return ", ".join(parts) or "-"
    if isinstance(value, list):
        return ", ".join(str(item) for item in value) or "-"
    text = re.sub(r"\s+", " ", str(value or "")).strip()
    return text or "-"


def _thread_metadata(thread_id: str, events: Sequence[Mapping[str, Any]]) -> Dict[str, str]:
    title = next((str(event.get("thread_title")) for event in events if event.get("thread_title")), thread_id)
    project_ids = list(
        dict.fromkeys(
            str(event.get("bridge_project_id"))
            for event in events
            if event.get("bridge_project_id")
        )
    )
    codex_ids = list(dict.fromkeys(str(event.get("codex_session_id")) for event in events if event.get("codex_session_id")))
    gpt_ids = list(dict.fromkeys(str(event.get("gpt_pro_session_id")) for event in events if event.get("gpt_pro_session_id")))
    return {
        "bridge_thread_id": thread_id,
        "bridge_project_id": ", ".join(project_ids),
        "title": title,
        "created_at": str(events[0].get("occurred_at", "")) if events else "",
        "last_used_at": str(events[-1].get("occurred_at", "")) if events else "",
        "codex_session_ids": ", ".join(codex_ids),
        "gpt_pro_session_ids": ", ".join(gpt_ids),
        "latest_event": str(events[-1].get("event_type", "")) if events else "",
        "event_count": str(len(events)),
    }


def render_thread_markdown(thread_id: str, events: Sequence[Mapping[str, Any]]) -> str:
    meta = _thread_metadata(thread_id, events)
    header = "\n".join(f"{key}: {json_quote(value)}" for key, value in meta.items())
    timeline: List[str] = []
    for index, event in enumerate(events, start=1):
        timeline.extend(
            [
                f"### {index:03d} · {event.get('occurred_at', '-')} · {event.get('event_type', 'event')} · `{event.get('event_id', '-')}`",
                "",
                f"- Actor: `{event.get('actor', '-')}`",
                f"- Parent: `{event.get('parent_event_id') or '-'}`",
            ]
        )
        if event.get("codex_session_id"):
            timeline.append(f"- Codex session: `{event['codex_session_id']}`")
        if event.get("gpt_pro_session_id"):
            timeline.append(f"- GPT Pro session: `{event['gpt_pro_session_id']}`")
        if event.get("bridge_project_id"):
            timeline.append(f"- Bridge project: `{event['bridge_project_id']}`")
        artifact = event.get("artifact")
        if artifact:
            timeline.append(f"- Artifact: {_format_value(artifact)}")
        data = event.get("data")
        if isinstance(data, Mapping):
            for key, value in data.items():
                if value not in (None, "", [], {}):
                    timeline.append(f"- {key.replace('_', ' ').title()}: {_format_value(value)}")
        timeline.append("")
    body = "\n".join(timeline).rstrip() or "_No events recorded._"
    return (
        f"{header}\n\n# Bridge Thread: {meta['title']}\n\n"
        f"## Sequence\n\n{_sequence_diagram(events)}\n\n"
        f"## Timeline\n\n{body}\n"
    )


def _write_thread_index(bridge_dir: Path) -> None:
    threads_dir = bridge_dir / "threads"
    threads_dir.mkdir(parents=True, exist_ok=True)
    with file_lock(threads_dir / ".index.lock"):
        stems = {p.stem for p in threads_dir.glob("*.jsonl")}
        stems.update(p.stem for p in threads_dir.glob("*.md") if p.name != "index.md")
        rows: List[Dict[str, str]] = []
        for stem in stems:
            try:
                rows.append(_thread_metadata(stem, load_events(bridge_dir, stem)))
            except BridgeError as exc:
                rows.append(
                    {
                        "bridge_thread_id": stem,
                        "bridge_project_id": "",
                        "title": "INVALID LEDGER",
                        "created_at": "",
                        "last_used_at": "",
                        "codex_session_ids": "",
                        "gpt_pro_session_ids": "",
                        "latest_event": f"ERROR: {exc}",
                        "event_count": "?",
                    }
                )
        rows.sort(key=lambda row: row.get("last_used_at", ""), reverse=True)
        lines = [
            "# Codex Pro Bridge Threads",
            "",
            "| Bridge Thread | Project | Title | Codex Sessions | GPT Pro Sessions | Events | Last Used | Latest Event | File |",
            "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
        ]
        for row in rows:
            escape = lambda value: (value or "-").replace("|", "\\|").replace("\n", " ")
            lines.append(
                "| `{}` | `{}` | {} | {} | {} | {} | {} | {} | [open]({}.md) |".format(
                    escape(row["bridge_thread_id"]),
                    escape(row["bridge_project_id"]),
                    escape(row["title"]),
                    escape(row["codex_session_ids"]),
                    escape(row["gpt_pro_session_ids"]),
                    escape(row["event_count"]),
                    escape(row["last_used_at"]),
                    escape(row["latest_event"]),
                    escape(row["bridge_thread_id"]),
                )
            )
        atomic_write_text(threads_dir / "index.md", "\n".join(lines) + "\n")


def append_event(
    repo: Path,
    *,
    thread_id: str,
    event_type: str,
    actor: str,
    thread_title: str = "",
    bridge_project_id: str = "",
    codex_session_id: str = "",
    gpt_pro_session_id: str = "",
    artifact: Mapping[str, Any] | None = None,
    data: Mapping[str, Any] | None = None,
    dedupe_key: str = "",
    occurred_at: str = "",
) -> Dict[str, Any]:
    repo = repo.resolve()
    thread_id = validate_id(thread_id, "bridge thread id")
    if bridge_project_id:
        bridge_project_id = validate_id(bridge_project_id, "bridge project id")
    if codex_session_id:
        codex_session_id = validate_id(codex_session_id, "Codex session id")
    if gpt_pro_session_id:
        gpt_pro_session_id = validate_id(gpt_pro_session_id, "GPT Pro session id")
    bridge_dir = bridge_root(repo)
    threads_dir = bridge_dir / "threads"
    threads_dir.mkdir(parents=True, exist_ok=True)
    jsonl_path = _jsonl_path(bridge_dir, thread_id)
    lock_path = threads_dir / f".{thread_id}.lock"
    with file_lock(lock_path):
        events = load_events(bridge_dir, thread_id)
        if not jsonl_path.exists() and events:
            atomic_write_text(
                jsonl_path,
                "".join(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n" for event in events),
            )
        existing_project_ids = {
            str(event.get("bridge_project_id"))
            for event in events
            if event.get("bridge_project_id")
        }
        if len(existing_project_ids) > 1:
            raise BridgeError(
                f"Bridge Thread {thread_id} has conflicting Project identities"
            )
        if (
            bridge_project_id
            and existing_project_ids
            and bridge_project_id not in existing_project_ids
        ):
            raise BridgeError(
                f"Bridge Thread {thread_id} belongs to "
                f"{next(iter(existing_project_ids))}, not {bridge_project_id}"
            )
        effective_project_id = bridge_project_id or (
            next(iter(existing_project_ids)) if existing_project_ids else ""
        )
        if dedupe_key:
            for event in events:
                if event.get("dedupe_key") == dedupe_key:
                    return event
        parent = str(events[-1].get("event_id", "")) if events else ""
        timestamp = occurred_at or now_iso()
        event = {
            "schema_version": SCHEMA_VERSION,
            "event_id": f"{timestamp.replace(':', '').replace('+', '-')}-{uuid.uuid4().hex[:10]}",
            "thread_id": thread_id,
            "event_type": event_type,
            "occurred_at": timestamp,
            "actor": actor,
            "parent_event_id": parent,
            "thread_title": thread_title or (events[0].get("thread_title", "") if events else thread_id),
            "codex_session_id": codex_session_id,
            "gpt_pro_session_id": gpt_pro_session_id,
            "artifact": dict(artifact or {}),
            "data": dict(data or {}),
            "dedupe_key": dedupe_key,
        }
        if effective_project_id:
            event["bridge_project_id"] = effective_project_id
        with jsonl_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
        events.append(event)
        atomic_write_text(_markdown_path(bridge_dir, thread_id), render_thread_markdown(thread_id, events))
    _write_thread_index(bridge_dir)
    return event


def compact_thread_context(
    repo: Path,
    thread_id: str,
    *,
    max_events: int = 24,
    max_chars: int = 20_000,
) -> str:
    if max_events <= 0 or max_chars <= 0:
        raise BridgeError("Thread event and character budgets must be positive")
    bridge_dir = bridge_root(repo)
    events = load_events(bridge_dir, thread_id)
    if not events:
        return "_No prior bridge thread events were available._"
    selected = events[-max_events:]
    omitted = len(events) - len(selected)
    while selected:
        lines = [
            f"- Bridge thread id: `{thread_id}`",
            f"- Total events: {len(events)}",
            f"- Included events: latest {len(selected)}",
            f"- Older events omitted: {omitted}",
            "",
            "## Recent Sequence",
            "",
            _sequence_diagram(selected),
            "",
            "## Recent Events",
            "",
        ]
        start = len(events) - len(selected) + 1
        for offset, event in enumerate(selected):
            lines.append(
                f"### {start + offset:03d} · {event.get('event_type')} · {event.get('occurred_at')}"
            )
            lines.append(f"- Event ID: `{event.get('event_id') or '-'}`")
            lines.append(f"- Parent: `{event.get('parent_event_id') or '-'}`")
            lines.append(f"- Summary: {_event_summary(event)}")
            artifact = event.get("artifact")
            if artifact:
                lines.append(f"- Artifact: {_format_value(artifact)}")
            data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
            for key in (
                "bundle_sha256",
                "model_verification",
                "requested_model",
                "selected_ui_label",
            ):
                if data.get(key) not in (None, ""):
                    lines.append(f"- {key.replace('_', ' ').title()}: {_format_value(data[key])}")
            lines.append("")
        content = "\n".join(lines).strip()
        if len(content) <= max_chars:
            return content
        selected = selected[1:]
        omitted += 1
    return "_Bridge thread exists, but its compact view exceeded the configured budget._"


def verify_thread_integrity(
    repo: Path,
    thread_id: str,
    *,
    require_complete_rounds: bool = False,
) -> Dict[str, Any]:
    """Verify ledger links, artifact digests, bundle digests, and round ordering."""
    repo = repo.resolve()
    thread_id = validate_id(thread_id, "bridge thread id")
    events = load_events(bridge_root(repo), thread_id)
    if not events:
        raise BridgeError(f"Bridge thread has no events: {thread_id}")

    event_ids: set[str] = set()
    dedupe_keys: set[str] = set()
    expected_parent = ""
    round_has_snapshot = False
    pending_exchange = False
    complete_rounds = 0
    artifact_count = 0
    bundle_count = 0
    project_ids: set[str] = set()

    for index, event in enumerate(events, start=1):
        prefix = f"event {index}"
        event_type = str(event.get("event_type", ""))
        expected_actor = {
            "codex-snapshot": "codex",
            "gpt-exchange": "gpt-pro",
            "codex-verdict": "codex",
        }.get(event_type)
        if expected_actor and event.get("actor") != expected_actor:
            raise BridgeError(
                f"{prefix}: actor mismatch for {event_type}; expected {expected_actor}"
            )
        if event.get("schema_version") != SCHEMA_VERSION:
            raise BridgeError(f"{prefix}: unsupported schema version")
        if event.get("thread_id") != thread_id:
            raise BridgeError(f"{prefix}: thread id mismatch")
        if event.get("bridge_project_id"):
            project_id = validate_id(
                str(event["bridge_project_id"]), "bridge project id"
            )
            project_ids.add(project_id)
            if len(project_ids) > 1:
                raise BridgeError(f"{prefix}: conflicting Bridge Project identity")
        event_id = str(event.get("event_id", ""))
        if not event_id or event_id in event_ids:
            raise BridgeError(f"{prefix}: missing or duplicate event id")
        event_ids.add(event_id)
        if str(event.get("parent_event_id", "")) != expected_parent:
            raise BridgeError(
                f"{prefix}: parent chain mismatch; expected {expected_parent or '<root>'}"
            )
        expected_parent = event_id
        occurred_at = str(event.get("occurred_at", ""))
        try:
            parsed_time = dt.datetime.fromisoformat(occurred_at)
        except ValueError as exc:
            raise BridgeError(f"{prefix}: invalid occurred_at timestamp") from exc
        if parsed_time.tzinfo is None:
            raise BridgeError(f"{prefix}: occurred_at must include a timezone")
        dedupe_key = str(event.get("dedupe_key", ""))
        if dedupe_key:
            if dedupe_key in dedupe_keys:
                raise BridgeError(f"{prefix}: duplicate dedupe key")
            dedupe_keys.add(dedupe_key)

        artifact = event.get("artifact")
        if artifact:
            if not isinstance(artifact, Mapping):
                raise BridgeError(f"{prefix}: artifact must be an object")
            artifact_path = str(artifact.get("path", ""))
            expected_sha = str(artifact.get("sha256", ""))
            if not artifact.get("kind") or not artifact_path or not re.fullmatch(
                r"[0-9a-f]{64}", expected_sha
            ):
                raise BridgeError(f"{prefix}: artifact kind, path, and SHA-256 are required")
            expected_kind = {
                "codex-snapshot": "codex-notes",
                "gpt-exchange": "gpt-pro-turn",
                "codex-verdict": "codex-verdict",
            }.get(event_type)
            if expected_kind and artifact.get("kind") != expected_kind:
                raise BridgeError(
                    f"{prefix}: artifact kind mismatch for {event_type}; expected {expected_kind}"
                )
            try:
                resolved_artifact = resolve_repo_path(artifact_path, repo)
            except BridgeError as exc:
                raise BridgeError(f"{prefix}: artifact path is missing or unsafe: {artifact_path}") from exc
            if not resolved_artifact.is_file():
                raise BridgeError(f"{prefix}: artifact is not a file: {artifact_path}")
            if file_sha256(resolved_artifact) != expected_sha:
                raise BridgeError(f"{prefix}: artifact hash mismatch: {artifact_path}")
            artifact_count += 1

        data = event.get("data") if isinstance(event.get("data"), Mapping) else {}
        bundle_path = str(data.get("bundle", ""))
        bundle_sha = str(data.get("bundle_sha256", ""))
        if bundle_path:
            if not re.fullmatch(r"[0-9a-f]{64}", bundle_sha):
                raise BridgeError(f"{prefix}: bundle SHA-256 is missing or invalid")
            try:
                bundle = resolve_repo_path(bundle_path, repo)
            except BridgeError as exc:
                raise BridgeError(f"{prefix}: bundle path is missing or unsafe: {bundle_path}") from exc
            if not bundle.is_file() or file_sha256(bundle) != bundle_sha:
                raise BridgeError(f"{prefix}: bundle hash mismatch: {bundle_path}")
            bundle_count += 1

        if event_type == "codex-snapshot":
            if pending_exchange:
                raise BridgeError(f"{prefix}: snapshot cannot precede the pending Codex verdict")
            round_has_snapshot = True
        elif event_type == "gpt-exchange":
            if not round_has_snapshot or pending_exchange:
                raise BridgeError(f"{prefix}: GPT exchange has no available Codex snapshot")
            pending_exchange = True
        elif event_type == "codex-verdict":
            if not pending_exchange:
                raise BridgeError(f"{prefix}: Codex verdict has no pending GPT exchange")
            pending_exchange = False
            round_has_snapshot = False
            complete_rounds += 1
        elif not event_type.startswith("legacy-"):
            raise BridgeError(f"{prefix}: unsupported event type {event_type!r}")

    if require_complete_rounds and (pending_exchange or round_has_snapshot):
        raise BridgeError("Bridge thread ends with an incomplete round")
    return {
        "valid": True,
        "thread_id": thread_id,
        "bridge_project_id": next(iter(project_ids)) if project_ids else "",
        "event_count": len(events),
        "complete_rounds": complete_rounds,
        "artifact_count": artifact_count,
        "bundle_count": bundle_count,
        "round_complete": not pending_exchange and not round_has_snapshot,
    }


def write_session_index(sessions_dir: Path, *, kind: str) -> None:
    sessions_dir.mkdir(parents=True, exist_ok=True)
    with file_lock(sessions_dir / ".index.lock"):
        rows = [parse_metadata(path) for path in sessions_dir.glob("*/session.md")]
        rows = [row for row in rows if row]
        rows.sort(key=lambda row: row.get("last_used_at", ""), reverse=True)
        escape = lambda value: (str(value or "-")).replace("|", "\\|").replace("\n", " ")
        if kind == "codex":
            lines = [
                "# Codex Bridge Sessions",
                "",
                "| Codex Session | Bridge Thread | Project | Title | Source | Last Used | Notes |",
                "| --- | --- | --- | --- | --- | --- | --- |",
            ]
            for row in rows:
                notes = row.get("notes_path", "")
                link = f"[notes]({notes})" if notes else "-"
                lines.append(
                    f"| `{escape(row.get('codex_session_id'))}` | `{escape(row.get('bridge_thread_id'))}` | "
                    f"`{escape(row.get('bridge_project_id'))}` | "
                    f"{escape(row.get('title'))} | {escape(row.get('history_source'))} | "
                    f"{escape(row.get('last_used_at'))} | {link} |"
                )
        elif kind == "gpt-pro":
            lines = [
                "# GPT Pro Bridge Sessions",
                "",
                "| GPT Pro Session | Bridge Thread | Project | Remote Project | Title | Purpose | Last Used | Latest Turn | URL |",
                "| --- | --- | --- | --- | --- | --- | --- | --- | --- |",
            ]
            for row in rows:
                url = row.get("web_conversation_url", "")
                link = f"[open]({url})" if url.startswith(("https://", "http://")) else "-"
                lines.append(
                    f"| `{escape(row.get('gpt_pro_session_id'))}` | `{escape(row.get('bridge_thread_id'))}` | "
                    f"`{escape(row.get('bridge_project_id'))}` | "
                    f"`{escape(row.get('remote_project_id'))}` | "
                    f"{escape(row.get('web_title'))} | {escape(row.get('purpose'))} | "
                    f"{escape(row.get('last_used_at'))} | {escape(row.get('latest_turn'))} | {link} |"
                )
        else:
            raise BridgeError(f"Unknown session index kind: {kind}")
        atomic_write_text(sessions_dir / "index.md", "\n".join(lines) + "\n")


def _short(value: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text[: limit - 3].rstrip() + "..." if len(text) > limit else text or "-"


def record_codex_verdict(
    repo: Path,
    *,
    thread_id: str,
    gpt_pro_session_id: str,
    codex_session_id: str,
    bridge_project_id: str = "",
    turn_path: Path,
    summary: str,
    verification: str,
    decision_trail: str = "",
    changes: str = "",
    tests: str = "",
    next_question: str = "",
    occurred_at: str = "",
) -> Path:
    """Write an immutable Codex verdict artifact and append its event."""
    repo = repo.resolve()
    thread_id = validate_id(thread_id, "bridge thread id")
    gpt_pro_session_id = validate_id(gpt_pro_session_id, "GPT Pro session id")
    codex_session_id = validate_id(codex_session_id, "Codex session id")
    turn_path = turn_path.resolve()
    if not is_within(turn_path, repo) or not turn_path.is_file():
        raise BridgeError("Verdict turn file must exist under the repository root")
    if not verification.strip():
        raise BridgeError("Codex verification is required before recording a verdict")
    session_meta = parse_metadata(
        bridge_root(repo) / "gpt-pro-sessions" / gpt_pro_session_id / "session.md"
    )
    bridge_project_id = (
        bridge_project_id or session_meta.get("bridge_project_id", "")
    )
    if bridge_project_id:
        bridge_project_id = validate_id(bridge_project_id, "bridge project id")
    if session_meta.get("bridge_project_id") not in (
        None,
        "",
        bridge_project_id,
    ):
        raise BridgeError(
            f"GPT Pro session belongs to {session_meta['bridge_project_id']}, "
            f"not {bridge_project_id}"
        )
    if session_meta.get("bridge_thread_id") not in (None, "", thread_id):
        raise BridgeError(
            f"GPT Pro session {gpt_pro_session_id} is bound to "
            f"{session_meta['bridge_thread_id']}, not {thread_id}"
        )
    if session_meta.get("codex_session_id") not in (None, "", codex_session_id):
        raise BridgeError(
            f"GPT Pro session {gpt_pro_session_id} is linked to Codex session "
            f"{session_meta['codex_session_id']}, not {codex_session_id}"
        )
    expected_session_dir = (
        bridge_root(repo) / "gpt-pro-sessions" / gpt_pro_session_id
    ).resolve()
    turn_rel = repo_relative(turn_path, repo)
    if turn_path.parent != expected_session_dir:
        raise BridgeError("Verdict turn must stay inside the bound GPT Pro session")
    matching_exchange = next(
        (
            event
            for event in load_events(bridge_root(repo), thread_id)
            if event.get("event_type") == "gpt-exchange"
            and isinstance(event.get("artifact"), Mapping)
            and event["artifact"].get("path") == turn_rel
            and event.get("gpt_pro_session_id") == gpt_pro_session_id
            and event.get("codex_session_id") == codex_session_id
        ),
        None,
    )
    if matching_exchange is None:
        raise BridgeError("Verdict turn is not a captured exchange in the bound GPT Pro session")
    saved_at = occurred_at or now_iso()
    payload_fingerprint = hashlib.sha256(
        json.dumps(
            {
                "turn": turn_rel,
                "summary": summary,
                "verification": verification,
                "decision_trail": decision_trail,
                "changes": changes,
                "tests": tests,
                "next_question": next_question,
            },
            ensure_ascii=False,
            sort_keys=True,
        ).encode("utf-8")
    ).hexdigest()
    verdict_path = (
        turn_path.parent
        / "verdicts"
        / f"{turn_path.stem}-verdict-{payload_fingerprint[:12]}.md"
    )
    content = "\n".join(
        [
            f"# Codex Verdict for {turn_path.stem}",
            "",
            "## Metadata",
            f"- Bridge Thread ID: `{thread_id}`",
            f"- Bridge Project ID: `{bridge_project_id or '-'}`",
            f"- Codex Session ID: `{codex_session_id}`",
            f"- GPT Pro Session ID: `{gpt_pro_session_id}`",
            f"- GPT Pro Turn: `{repo_relative(turn_path, repo)}`",
            f"- Recorded at: {saved_at}",
            "",
            "## Codex Summary",
            "",
            summary.strip() or "_Not recorded._",
            "",
            "## Codex Verification",
            "",
            verification.strip(),
            "",
            "## Decision Trail",
            "",
            decision_trail.strip() or "_Not recorded._",
            "",
            "## Implemented or Proposed Changes",
            "",
            changes.strip() or "_None recorded._",
            "",
            "## Tests and Validation",
            "",
            tests.strip() or "_None recorded._",
            "",
            "## Next GPT Pro Question",
            "",
            next_question.strip() or "_No next question recorded._",
            "",
        ]
    )
    if not verdict_path.exists():
        atomic_write_text(verdict_path, content)
    verdict_rel = repo_relative(verdict_path, repo)
    append_event(
        repo,
        thread_id=thread_id,
        event_type="codex-verdict",
        actor="codex",
        thread_title=session_meta.get("purpose", "") or session_meta.get("web_title", "") or thread_id,
        bridge_project_id=bridge_project_id,
        codex_session_id=codex_session_id,
        gpt_pro_session_id=gpt_pro_session_id,
        artifact={"kind": "codex-verdict", "path": verdict_rel, "sha256": file_sha256(verdict_path)},
        data={
            "turn": repo_relative(turn_path, repo),
            "summary": _short(summary),
            "verification": _short(verification),
            "next_question": _short(next_question),
        },
        dedupe_key=f"codex-verdict:{turn_rel}:{payload_fingerprint}",
        occurred_at=saved_at,
    )
    return verdict_path
