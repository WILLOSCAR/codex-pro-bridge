#!/usr/bin/env python3
"""Write current Codex notes and record an immutable task snapshot."""

from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path


SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import (  # noqa: E402
    BridgeError,
    append_event,
    atomic_write_text,
    bridge_root,
    default_codex_session_id,
    file_sha256,
    now_iso,
    parse_metadata,
    repo_relative,
    unique_artifact_path,
    validate_id,
    write_bound_metadata,
    write_session_index,
)


def read_value(text: str, file_path: str) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    return (text or "").strip()


def section(title: str, body: str) -> str:
    return f"## {title}\n\n{body.strip() if body.strip() else '_Not recorded._'}\n"


def one_line(value: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text[: limit - 3].rstrip() + "..." if len(text) > limit else text or "-"


def build_notes(
    *,
    thread_id: str,
    codex_session_id: str,
    title: str,
    goal: str,
    question: str,
    summary: str,
    raw_history: str,
    history_source: str,
    created_at: str,
    updated_at: str,
) -> str:
    raw = raw_history or (
        "_Raw Codex turns were not included. The detailed summary is the complete "
        "Codex-side context for this snapshot._"
    )
    return "\n".join(
        [
            "# Codex Session Notes",
            "",
            "## Metadata",
            f"- Bridge Thread ID: `{thread_id}`",
            f"- Codex Session ID: `{codex_session_id}`",
            f"- Title: {title}",
            f"- Created at: {created_at}",
            f"- Updated at: {updated_at}",
            f"- History source: {history_source}",
            "",
            section("Goal", goal),
            section("GPT Pro Question", question),
            section("Detailed Session Summary", summary),
            section("Recent Raw Conversation", raw),
            section(
                "Evidence Rules",
                "\n".join(
                    [
                        "- Treat the detailed summary as primary context.",
                        "- Use raw turns only for nuance; they may be incomplete.",
                        "- Do not infer repository facts that are absent from the attached evidence.",
                    ]
                ),
            ),
        ]
    ).rstrip() + "\n"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Write current Codex notes and append an immutable codex-snapshot event."
    )
    parser.add_argument("--repo", default=".", help="Repository root.")
    parser.add_argument("--bridge-thread-id", required=True, help="Canonical task id.")
    parser.add_argument("--codex-session-id", default="", help="Defaults to <bridge-thread-id>-codex.")
    parser.add_argument("--title", default="", help="Task title.")
    parser.add_argument("--goal", default="", help="User goal.")
    parser.add_argument("--goal-file", default="", help="File containing the user goal.")
    parser.add_argument("--gpt-pro-question", default="", help="Question intended for GPT Pro.")
    parser.add_argument("--gpt-pro-question-file", default="", help="File containing the question.")
    parser.add_argument("--summary", default="", help="Detailed Codex summary.")
    parser.add_argument("--summary-file", default="", help="File containing the summary.")
    parser.add_argument("--raw-history", default="", help="Optional recent raw turns.")
    parser.add_argument("--raw-history-file", default="", help="File containing optional raw turns.")
    parser.add_argument("--history-source", default="", help="Examples: visible-codex-context, exported-transcript.")
    args = parser.parse_args()

    try:
        repo = Path(args.repo).resolve()
        if not repo.is_dir():
            raise BridgeError(f"Repository root is not a directory: {repo}")
        thread_id = validate_id(args.bridge_thread_id, "bridge thread id")
        codex_session_id = validate_id(
            args.codex_session_id or default_codex_session_id(thread_id), "Codex session id"
        )
        goal = read_value(args.goal, args.goal_file)
        question = read_value(args.gpt_pro_question, args.gpt_pro_question_file)
        summary = read_value(args.summary, args.summary_file)
        raw_history = read_value(args.raw_history, args.raw_history_file)
        if not summary:
            raise BridgeError("Provide --summary or --summary-file")
        if args.history_source and args.history_source != "unavailable" and not raw_history:
            raise BridgeError(
                "A non-unavailable --history-source requires --raw-history or --raw-history-file"
            )

        bridge_dir = bridge_root(repo)
        sessions_dir = bridge_dir / "codex-sessions"
        session_dir = sessions_dir / codex_session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        session_path = session_dir / "session.md"
        notes_path = session_dir / "notes.md"
        previous = parse_metadata(session_path)
        if previous.get("bridge_thread_id") not in (None, "", thread_id):
            raise BridgeError(
                f"Codex session {codex_session_id} is already bound to "
                f"{previous['bridge_thread_id']}; refusing to move it to {thread_id}"
            )

        now = now_iso()
        created_at = previous.get("created_at", "") or now
        title = one_line(args.title or previous.get("title", "") or goal or thread_id, 120)
        history_source = args.history_source or (
            "visible-codex-context" if raw_history else "unavailable"
        )
        notes = build_notes(
            thread_id=thread_id,
            codex_session_id=codex_session_id,
            title=title,
            goal=goal,
            question=question,
            summary=summary,
            raw_history=raw_history,
            history_source=history_source,
            created_at=created_at,
            updated_at=now,
        )
        snapshot_path = unique_artifact_path(session_dir / "snapshots", "notes", ".md")
        atomic_write_text(snapshot_path, notes)
        atomic_write_text(notes_path, notes)

        write_bound_metadata(
            session_path,
            {
                "codex_session_id": codex_session_id,
                "bridge_thread_id": thread_id,
                "title": title,
                "history_source": history_source,
                "created_at": created_at,
                "last_used_at": now,
                "notes_path": repo_relative(notes_path, repo),
                "latest_snapshot": repo_relative(snapshot_path, repo),
            },
            ordered_keys=(
                "codex_session_id",
                "bridge_thread_id",
                "title",
                "history_source",
                "created_at",
                "last_used_at",
                "notes_path",
                "latest_snapshot",
            ),
            immutable_keys=("codex_session_id", "bridge_thread_id", "created_at"),
        )
        write_session_index(sessions_dir, kind="codex")
        snapshot_rel = repo_relative(snapshot_path, repo)
        append_event(
            repo,
            thread_id=thread_id,
            event_type="codex-snapshot",
            actor="codex",
            thread_title=title,
            codex_session_id=codex_session_id,
            artifact={
                "kind": "codex-notes",
                "path": snapshot_rel,
                "sha256": file_sha256(snapshot_path),
            },
            data={
                "goal": one_line(goal),
                "summary": one_line(summary),
                "question": one_line(question),
                "history_source": history_source,
                "current_notes": repo_relative(notes_path, repo),
            },
            dedupe_key=f"codex-snapshot:{snapshot_rel}",
            occurred_at=now,
        )
        print(notes_path)
        print(f"Immutable snapshot: {snapshot_path}", file=sys.stderr)
        return 0
    except (BridgeError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
