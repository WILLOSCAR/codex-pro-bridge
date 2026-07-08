#!/usr/bin/env python3
"""Prepare Codex-side session notes for a Codex Pro Bridge bundle."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
from pathlib import Path
from typing import Dict, Iterable, List

GRAPH_EVENT_LIMIT = 40
GRAPH_LABELS = {
    "codex-update": "codex",
    "bundle": "bundle",
    "gpt-pro-turn": "gpt",
}


def slugify(value: str, fallback: str) -> str:
    slug = re.sub(r"[^A-Za-z0-9]+", "-", value.strip().lower()).strip("-")
    return (slug or fallback)[:80].strip("-") or fallback


def derive_bridge_thread_id(explicit: str, *candidates: str) -> str:
    if explicit:
        return slugify(explicit, "bridge-thread")
    for candidate in candidates:
        base = re.sub(r"-(codex|gpt-pro)$", "", (candidate or "").strip())
        if base:
            return slugify(base, "bridge-thread")
    return "bridge-thread"


def read_text(text: str, file_path: str) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    return (text or "").strip()


def yaml_quote(value: str) -> str:
    return json.dumps(value or "", ensure_ascii=False)


def parse_session(path: Path) -> Dict[str, str]:
    if not path.exists():
        return {}
    result: Dict[str, str] = {}
    for line in path.read_text(encoding="utf-8").splitlines():
        if line.startswith("# ") or line == "## Timeline":
            break
        if ":" not in line or line.lstrip().startswith("#"):
            continue
        key, raw = line.split(":", 1)
        key = key.strip()
        raw = raw.strip()
        if not key:
            continue
        try:
            result[key] = json.loads(raw)
        except Exception:
            result[key] = raw.strip("'\"")
    return result


def escape_table(value: str) -> str:
    return (value or "-").replace("\n", " ").replace("|", "\\|")


def merge_csv(existing: str, *values: str) -> str:
    merged: List[str] = []
    for value in [existing, *values]:
        for item in (value or "").split(","):
            item = item.strip()
            if item and item not in merged:
                merged.append(item)
    return ", ".join(merged)


def one_line(value: str, limit: int = 160) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    if len(text) > limit:
        return text[: limit - 3].rstrip() + "..."
    return text or "-"


def parse_timeline_events(timeline: str) -> List[Dict[str, object]]:
    events: List[Dict[str, object]] = []
    current: Dict[str, object] | None = None
    for line in timeline.splitlines():
        if line.startswith("### "):
            if current:
                events.append(current)
            header = line[4:].strip()
            if " - " in header:
                timestamp, event_type = header.split(" - ", 1)
            else:
                timestamp, event_type = "", header
            current = {"timestamp": timestamp, "event_type": event_type, "lines": []}
        elif current is not None:
            current["lines"].append(line)
    if current:
        events.append(current)
    return events


def graph_label(index: int, event_type: str) -> str:
    label = GRAPH_LABELS.get(event_type, slugify(event_type, "event")[:8])
    return f"{index:02d} {label}"


def build_git_graph(timeline: str, max_events: int = GRAPH_EVENT_LIMIT) -> str:
    events = parse_timeline_events(timeline)
    selected = events[-max_events:]
    start_index = max(1, len(events) - len(selected) + 1)
    lines = ["```mermaid", "gitGraph", "  commit id: \"start\""]
    gpt_branch_created = False
    for offset, event in enumerate(selected):
        event_type = str(event.get("event_type", "event"))
        label = graph_label(start_index + offset, event_type)
        if event_type == "gpt-pro-turn":
            if not gpt_branch_created:
                lines.append("  branch gpt-pro")
                gpt_branch_created = True
            lines.append("  checkout gpt-pro")
            lines.append(f"  commit id: {yaml_quote(label)}")
            lines.append("  checkout main")
        else:
            lines.append(f"  commit id: {yaml_quote(label)}")
    lines.append("```")
    if len(events) > len(selected):
        return f"_Graph shows the latest {len(selected)} of {len(events)} events._\n\n" + "\n".join(lines)
    return "\n".join(lines)


def write_index(codex_sessions_dir: Path) -> None:
    rows: List[Dict[str, str]] = []
    for session_file in codex_sessions_dir.glob("*/session.md"):
        meta = parse_session(session_file)
        if meta:
            rows.append(meta)
    rows.sort(key=lambda item: item.get("last_used_at", ""), reverse=True)

    lines = [
        "# Codex Bridge Sessions",
        "",
        "| Codex Session | Bridge Thread | Title | Source | Last Used | Notes |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for meta in rows:
        session_id = meta.get("codex_session_id", "")
        notes_path = meta.get("notes_path", "")
        notes_link = f"[notes]({notes_path})" if notes_path else "-"
        lines.append(
            "| `{}` | `{}` | {} | {} | {} | {} |".format(
                escape_table(session_id),
                escape_table(meta.get("bridge_thread_id", "")),
                escape_table(meta.get("title", "")),
                escape_table(meta.get("history_source", "")),
                escape_table(meta.get("last_used_at", "")),
                notes_link,
            )
        )
    lines.append("")
    (codex_sessions_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")


def write_thread_index(threads_dir: Path) -> None:
    rows: List[Dict[str, str]] = []
    for thread_file in threads_dir.glob("*.md"):
        if thread_file.name == "index.md":
            continue
        meta = parse_session(thread_file)
        if meta:
            meta["_file"] = thread_file.name
            rows.append(meta)
    rows.sort(key=lambda item: item.get("last_used_at", ""), reverse=True)

    lines = [
        "# Codex Pro Bridge Threads",
        "",
        "| Bridge Thread | Title | Codex Sessions | GPT Pro Sessions | Last Used | Latest Event | File |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for meta in rows:
        file_name = meta.get("_file", "")
        link = f"[open]({file_name})" if file_name else "-"
        lines.append(
            "| `{}` | {} | {} | {} | {} | {} | {} |".format(
                escape_table(meta.get("bridge_thread_id", "")),
                escape_table(meta.get("title", "")),
                escape_table(meta.get("codex_session_ids", "")),
                escape_table(meta.get("gpt_pro_session_ids", "")),
                escape_table(meta.get("last_used_at", "")),
                escape_table(meta.get("latest_event", "")),
                link,
            )
        )
    lines.append("")
    (threads_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")


def append_thread_event(
    bridge_dir: Path,
    bridge_thread_id: str,
    title: str,
    now: str,
    event_type: str,
    details: List[str],
    codex_session_id: str = "",
    gpt_pro_session_id: str = "",
) -> Path:
    threads_dir = bridge_dir / "threads"
    threads_dir.mkdir(parents=True, exist_ok=True)
    thread_path = threads_dir / f"{bridge_thread_id}.md"
    previous = parse_session(thread_path)
    previous_text = thread_path.read_text(encoding="utf-8") if thread_path.exists() else ""
    timeline = ""
    marker = "## Timeline\n"
    if marker in previous_text:
        timeline = previous_text.split(marker, 1)[1].strip()

    event = "\n".join([f"### {now} - {event_type}", "", *details]).strip()
    timeline = "\n\n".join(part for part in [timeline, event] if part).strip()
    graph = build_git_graph(timeline)
    meta = {
        "bridge_thread_id": bridge_thread_id,
        "title": previous.get("title", "") or title or bridge_thread_id,
        "created_at": previous.get("created_at", "") or now,
        "last_used_at": now,
        "codex_session_ids": merge_csv(previous.get("codex_session_ids", ""), codex_session_id),
        "gpt_pro_session_ids": merge_csv(previous.get("gpt_pro_session_ids", ""), gpt_pro_session_id),
        "latest_event": event_type,
    }
    header = "\n".join(f"{key}: {yaml_quote(value)}" for key, value in meta.items())
    thread_path.write_text(
        f"{header}\n\n# Bridge Thread: {meta['title']}\n\n## Graph\n\n{graph}\n\n## Timeline\n\n{timeline}\n",
        encoding="utf-8",
    )
    write_thread_index(threads_dir)
    return thread_path


def section(title: str, body: str) -> str:
    text = body.strip() if body.strip() else "_Not recorded._"
    return f"## {title}\n\n{text}\n"


def default_raw_history_note() -> str:
    return (
        "_Raw Codex conversation history was not available to this script. "
        "When Codex can see recent turns in the active thread, it should paste the last "
        "roughly 10 relevant user/assistant turns here. Do not invent missing turns._"
    )


def build_notes(
    bridge_thread_id: str,
    codex_session_id: str,
    title: str,
    goal: str,
    gpt_pro_question: str,
    summary: str,
    raw_history: str,
    history_source: str,
    created_at: str,
) -> str:
    return "\n".join(
        [
            "# Codex Session Notes",
            "",
            "## Metadata",
            f"- Bridge Thread ID: `{bridge_thread_id}`",
            f"- Codex Session ID: `{codex_session_id}`",
            f"- Title: {title or '-'}",
            f"- Created at: {created_at}",
            f"- History source: {history_source}",
            "",
            section("Goal", goal),
            section("GPT Pro Question", gpt_pro_question),
            section("Detailed Session Summary", summary),
            section("Recent Raw Conversation", raw_history or default_raw_history_note()),
            section(
                "How GPT Pro Should Use These Notes",
                "\n".join(
                    [
                        "- Treat the detailed summary as the primary Codex-side context.",
                        "- Use the raw conversation only to recover nuance that the summary may have compressed.",
                        "- Do not assume raw turns are complete unless the notes explicitly say so.",
                    ]
                ),
            ),
        ]
    ).rstrip() + "\n"


def first_present(values: Iterable[str]) -> str:
    for value in values:
        if value:
            return value
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Write Codex-side session notes under .codex/codex-pro-bridge/codex-sessions/.")
    parser.add_argument("--repo", default=".", help="Repository root. Default: current directory.")
    parser.add_argument("--bridge-thread-id", default="", help="Task-level id that links Codex notes, bundles, and GPT Pro turns.")
    parser.add_argument("--codex-session-id", required=True, help="Filesystem-safe Codex-side session id.")
    parser.add_argument("--title", default="", help="Short title for this Codex-side session.")
    parser.add_argument("--goal", default="", help="User goal or task.")
    parser.add_argument("--goal-file", default="", help="File containing the user goal or task.")
    parser.add_argument("--gpt-pro-question", default="", help="Question intended for GPT Pro.")
    parser.add_argument("--gpt-pro-question-file", default="", help="File containing the GPT Pro question.")
    parser.add_argument("--summary", default="", help="Detailed Codex session summary. Prefer --summary-file for long notes.")
    parser.add_argument("--summary-file", default="", help="File containing detailed Codex session summary.")
    parser.add_argument("--raw-history", default="", help="Recent raw Codex conversation turns. Prefer --raw-history-file for long notes.")
    parser.add_argument("--raw-history-file", default="", help="File containing recent raw Codex conversation turns.")
    parser.add_argument("--history-source", default="visible-codex-context", help="Examples: visible-codex-context, exported-transcript, unavailable.")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    codex_session_id = slugify(args.codex_session_id, "codex-session")
    bridge_dir = repo / ".codex" / "codex-pro-bridge"
    codex_sessions_dir = bridge_dir / "codex-sessions"
    session_dir = codex_sessions_dir / codex_session_id
    session_dir.mkdir(parents=True, exist_ok=True)

    notes_path = session_dir / "notes.md"
    session_path = session_dir / "session.md"
    previous = parse_session(session_path)
    now = dt.datetime.now().isoformat(timespec="seconds")

    goal = read_text(args.goal, args.goal_file)
    gpt_pro_question = read_text(args.gpt_pro_question, args.gpt_pro_question_file)
    summary = read_text(args.summary, args.summary_file)
    raw_history = read_text(args.raw_history, args.raw_history_file)
    if not summary:
        parser.error("Provide --summary or --summary-file. The Codex session summary is required.")

    title = first_present([args.title, previous.get("title", ""), goal[:80], codex_session_id])
    bridge_thread_id = derive_bridge_thread_id(args.bridge_thread_id, previous.get("bridge_thread_id", ""), codex_session_id)
    notes_path.write_text(
        build_notes(
            bridge_thread_id=bridge_thread_id,
            codex_session_id=codex_session_id,
            title=title,
            goal=goal,
            gpt_pro_question=gpt_pro_question,
            summary=summary,
            raw_history=raw_history,
            history_source=args.history_source,
            created_at=now,
        ),
        encoding="utf-8",
    )

    meta = {
        "codex_session_id": codex_session_id,
        "bridge_thread_id": bridge_thread_id,
        "title": title,
        "history_source": args.history_source,
        "created_at": first_present([previous.get("created_at", ""), now]),
        "last_used_at": now,
        "notes_path": notes_path.relative_to(repo).as_posix(),
    }
    session_path.write_text(
        "\n".join(f"{key}: {yaml_quote(value)}" for key, value in meta.items()) + "\n",
        encoding="utf-8",
    )
    write_index(codex_sessions_dir)
    append_thread_event(
        bridge_dir=bridge_dir,
        bridge_thread_id=bridge_thread_id,
        title=title,
        now=now,
        event_type="codex-update",
        details=[
            f"- Codex session: `{codex_session_id}`",
            f"- Notes: `{notes_path.relative_to(repo).as_posix()}`",
            f"- Goal: {one_line(goal)}",
            f"- Summary: {one_line(summary)}",
            f"- GPT Pro question: {one_line(gpt_pro_question)}",
        ],
        codex_session_id=codex_session_id,
    )

    print(notes_path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
