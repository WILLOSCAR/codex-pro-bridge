#!/usr/bin/env python3
"""Save a GPT Pro web response as a local GPT Pro session turn."""

from __future__ import annotations

import argparse
import datetime as dt
import json
import re
import sys
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


def read_value(text: str, file_path: str) -> str:
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


def next_turn_number(session_dir: Path) -> int:
    current = 0
    for path in session_dir.glob("[0-9][0-9][0-9]-*.md"):
        try:
            current = max(current, int(path.name[:3]))
        except ValueError:
            continue
    return current + 1


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


def write_index(sessions_dir: Path) -> None:
    rows: List[Dict[str, str]] = []
    for session_file in sessions_dir.glob("*/session.md"):
        meta = parse_session(session_file)
        if meta:
            rows.append(meta)
    rows.sort(key=lambda item: item.get("last_used_at", ""), reverse=True)

    lines = [
        "# GPT Pro Bridge Sessions",
        "",
        "| GPT Pro Session | Bridge Thread | Title | Purpose | Last Used | Latest Turn | URL |",
        "| --- | --- | --- | --- | --- | --- | --- |",
    ]
    for meta in rows:
        url = meta.get("web_conversation_url", "")
        link = f"[open]({url})" if url.startswith(("http://", "https://")) else "-"
        lines.append(
            "| `{}` | `{}` | {} | {} | {} | {} | {} |".format(
                escape_table(meta.get("gpt_pro_session_id", meta.get("session_id", ""))),
                escape_table(meta.get("bridge_thread_id", "")),
                escape_table(meta.get("web_title", "")),
                escape_table(meta.get("purpose", "")),
                escape_table(meta.get("last_used_at", "")),
                escape_table(meta.get("latest_turn", "")),
                link,
            )
        )
    lines.append("")
    (sessions_dir / "index.md").write_text("\n".join(lines), encoding="utf-8")


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


def write_session_file(path: Path, meta: Dict[str, str]) -> None:
    ordered = [
        "gpt_pro_session_id",
        "bridge_thread_id",
        "codex_session_id",
        "web_conversation_url",
        "web_title",
        "purpose",
        "created_at",
        "last_used_at",
        "latest_turn",
    ]
    lines = [f"{key}: {yaml_quote(meta.get(key, ''))}" for key in ordered]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def build_turn(
    number: int,
    title: str,
    bridge_thread_id: str,
    gpt_pro_session_id: str,
    codex_session_id: str,
    codex_notes: str,
    web_url: str,
    web_title: str,
    bundle: str,
    asked_at: str,
    saved_at: str,
    prompt: str,
    answer: str,
    summary: str,
    verification: str,
    decision_trail: str,
) -> str:
    number_text = f"{number:03d}"
    lines = [
        f"# {number_text} {title}",
        "",
        "## Metadata",
        f"- Bridge Thread ID: `{bridge_thread_id}`",
        f"- GPT Pro Session ID: `{gpt_pro_session_id}`",
        f"- Codex Session ID: `{codex_session_id or '-'}`",
        f"- GPT Pro URL: {web_url or '-'}",
        f"- Web Title: {web_title or '-'}",
        f"- Codex Notes: {codex_notes or '-'}",
        f"- Bundle: {bundle or '-'}",
        f"- Asked at: {asked_at}",
        f"- Saved at: {saved_at}",
        "",
        section("Prompt", prompt),
        section(
            "Evidence Boundary",
            "\n".join(
                [
                    f"- Bundle: {bundle or '-'}",
                    f"- Codex notes: {codex_notes or '-'}",
                    "- GPT Pro only saw the prompt and attached/pasted context.",
                    "- The local repository remains the source of truth.",
                ]
            ),
        ),
        section("GPT Pro Answer", answer),
        section("Codex Summary", summary),
        section("Codex Verification", verification),
        section("Decision Trail", decision_trail),
    ]
    return "\n".join(lines).rstrip() + "\n"


def existing_values(values: Iterable[str]) -> str:
    for value in values:
        if value:
            return value
    return ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Save a GPT Pro response under .codex/codex-pro-bridge/gpt-pro-sessions/.")
    parser.add_argument("--repo", default=".", help="Repository root. Default: current directory.")
    parser.add_argument("--bridge-thread-id", default="", help="Task-level id that links Codex notes, bundles, and GPT Pro turns.")
    parser.add_argument("--codex-session-id", default="", help="Codex-side session id that produced the prompt or bundle.")
    parser.add_argument("--codex-notes", default="", help="Codex-side notes path used for this turn, if known.")
    parser.add_argument("--gpt-pro-session-id", "--session-id", dest="gpt_pro_session_id", required=True, help="Filesystem-safe GPT Pro-side session id, for example repo-20260703-short-task-gpt-pro.")
    parser.add_argument("--web-url", default="", help="GPT Pro conversation URL.")
    parser.add_argument("--web-title", default="", help="Observed GPT Pro conversation title.")
    parser.add_argument("--purpose", default="", help="Why this GPT Pro session exists.")
    parser.add_argument("--turn-title", default="", help="Short title for this question/answer turn.")
    parser.add_argument("--bundle", default="", help="Bundle path used for this turn, if any.")
    parser.add_argument("--asked-at", default="", help="ISO timestamp for when GPT Pro was asked.")
    parser.add_argument("--prompt", default="", help="Prompt text. Prefer --prompt-file for long prompts.")
    parser.add_argument("--prompt-file", default="", help="File containing prompt text.")
    parser.add_argument("--answer", default="", help="Full GPT Pro answer. Prefer --answer-file for long answers.")
    parser.add_argument("--answer-file", default="", help="File containing the full GPT Pro answer.")
    parser.add_argument("--summary", default="", help="Codex summary of useful parts.")
    parser.add_argument("--summary-file", default="", help="File containing Codex summary.")
    parser.add_argument("--verification", default="", help="Codex local verification notes.")
    parser.add_argument("--verification-file", default="", help="File containing verification notes.")
    parser.add_argument("--decision-trail", default="", help="Assumptions, decisions, open questions, and next steps.")
    parser.add_argument("--decision-trail-file", default="", help="File containing decision-trail notes.")
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    bridge_dir = repo / ".codex" / "codex-pro-bridge"
    sessions_dir = bridge_dir / "gpt-pro-sessions"
    sessions_dir.mkdir(parents=True, exist_ok=True)

    gpt_pro_session_id = slugify(args.gpt_pro_session_id, "gpt-pro-session")
    if gpt_pro_session_id != args.gpt_pro_session_id:
        print(f"Using filesystem-safe GPT Pro session id: {gpt_pro_session_id}", file=sys.stderr)

    prompt = read_value(args.prompt, args.prompt_file)
    answer = read_value(args.answer, args.answer_file)
    if not prompt:
        parser.error("Provide --prompt or --prompt-file.")
    if not answer:
        parser.error("Provide --answer or --answer-file.")

    summary = read_value(args.summary, args.summary_file)
    verification = read_value(args.verification, args.verification_file)
    decision_trail = read_value(args.decision_trail, args.decision_trail_file)

    now = dt.datetime.now().isoformat(timespec="seconds")
    asked_at = args.asked_at or now
    session_dir = sessions_dir / gpt_pro_session_id
    session_dir.mkdir(parents=True, exist_ok=True)
    session_file = session_dir / "session.md"
    previous = parse_session(session_file)
    codex_session_id = slugify(args.codex_session_id, "codex-session") if args.codex_session_id else previous.get("codex_session_id", "")
    bridge_thread_id = derive_bridge_thread_id(args.bridge_thread_id, previous.get("bridge_thread_id", ""), codex_session_id, gpt_pro_session_id)

    number = next_turn_number(session_dir)
    latest_turn = f"{number:03d}"
    title = args.turn_title or args.web_title or args.purpose or gpt_pro_session_id
    title_slug = slugify(title, "turn")
    turn_file = session_dir / f"{latest_turn}-{title_slug}.md"

    meta = {
        "gpt_pro_session_id": gpt_pro_session_id,
        "bridge_thread_id": bridge_thread_id,
        "codex_session_id": codex_session_id,
        "web_conversation_url": existing_values([args.web_url, previous.get("web_conversation_url", "")]),
        "web_title": existing_values([args.web_title, previous.get("web_title", ""), title]),
        "purpose": existing_values([args.purpose, previous.get("purpose", ""), title]),
        "created_at": existing_values([previous.get("created_at", ""), now]),
        "last_used_at": now,
        "latest_turn": latest_turn,
    }

    write_session_file(session_file, meta)
    turn_file.write_text(
        build_turn(
            number=number,
            title=title,
            bridge_thread_id=bridge_thread_id,
            gpt_pro_session_id=gpt_pro_session_id,
            codex_session_id=codex_session_id,
            codex_notes=args.codex_notes,
            web_url=meta["web_conversation_url"],
            web_title=meta["web_title"],
            bundle=args.bundle,
            asked_at=asked_at,
            saved_at=now,
            prompt=prompt,
            answer=answer,
            summary=summary,
            verification=verification,
            decision_trail=decision_trail,
        ),
        encoding="utf-8",
    )
    write_index(sessions_dir)
    append_thread_event(
        bridge_dir=bridge_dir,
        bridge_thread_id=bridge_thread_id,
        title=meta["purpose"] or meta["web_title"] or title,
        now=now,
        event_type="gpt-pro-turn",
        details=[
            f"- GPT Pro session: `{gpt_pro_session_id}`",
            f"- Turn: `{turn_file.relative_to(repo).as_posix()}`",
            f"- Codex session: `{codex_session_id or '-'}`",
            f"- Codex notes: `{args.codex_notes}`" if args.codex_notes else "- Codex notes: -",
            f"- Bundle: `{args.bundle}`" if args.bundle else "- Bundle: -",
            f"- Summary: {one_line(summary)}",
        ],
        codex_session_id=codex_session_id,
        gpt_pro_session_id=gpt_pro_session_id,
    )

    print(turn_file)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
