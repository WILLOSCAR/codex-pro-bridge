#!/usr/bin/env python3
"""Record Codex verification as a separate immutable bridge event."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path


SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import (  # noqa: E402
    BridgeError,
    default_codex_session_id,
    default_gpt_session_id,
    record_codex_verdict,
    resolve_repo_path,
    validate_id,
)


def read_value(text: str, file_path: str) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    return (text or "").strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Record a Codex verdict for a captured GPT Pro turn.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--bridge-thread-id", required=True)
    parser.add_argument("--bridge-project-id", default="")
    parser.add_argument("--codex-session-id", default="")
    parser.add_argument("--gpt-pro-session-id", default="")
    parser.add_argument("--turn", required=True, help="Captured GPT Pro turn file under the repository root.")
    parser.add_argument("--summary", default="")
    parser.add_argument("--summary-file", default="")
    parser.add_argument("--verification", default="")
    parser.add_argument("--verification-file", default="")
    parser.add_argument("--decision-trail", default="")
    parser.add_argument("--decision-trail-file", default="")
    parser.add_argument("--changes", default="")
    parser.add_argument("--changes-file", default="")
    parser.add_argument("--tests", default="")
    parser.add_argument("--tests-file", default="")
    parser.add_argument("--next-question", default="")
    parser.add_argument("--next-question-file", default="")
    args = parser.parse_args()

    try:
        repo = Path(args.repo).resolve()
        thread_id = validate_id(args.bridge_thread_id, "bridge thread id")
        codex_session_id = validate_id(
            args.codex_session_id or default_codex_session_id(thread_id), "Codex session id"
        )
        gpt_session_id = validate_id(
            args.gpt_pro_session_id or default_gpt_session_id(thread_id), "GPT Pro session id"
        )
        verdict = record_codex_verdict(
            repo,
            thread_id=thread_id,
            gpt_pro_session_id=gpt_session_id,
            codex_session_id=codex_session_id,
            bridge_project_id=args.bridge_project_id,
            turn_path=resolve_repo_path(args.turn, repo),
            summary=read_value(args.summary, args.summary_file),
            verification=read_value(args.verification, args.verification_file),
            decision_trail=read_value(args.decision_trail, args.decision_trail_file),
            changes=read_value(args.changes, args.changes_file),
            tests=read_value(args.tests, args.tests_file),
            next_question=read_value(args.next_question, args.next_question_file),
        )
        print(verdict)
        return 0
    except (BridgeError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
