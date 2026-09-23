#!/usr/bin/env python3
"""Open one high-frequency, standalone GPT Pro Review Probe.

A Review Probe is the fast lane for high-frequency, fine-grained, parallel
review of a single idea, research proposal, or atomic sub-task. It is a
standalone, single-round Bridge Thread that NEVER attaches to a Bridge Project,
so it never consults Project source-sync and is never blocked when unrelated
shared sources are stale. Many probes run in parallel because each has a distinct
thread, conversation, ledger, and durable tab owner token. Host-local claims
reject a second owner only for the same conversation.

This orchestrator prepares the immutable Codex snapshot and builds the standalone
evidence bundle. It deliberately does NOT import BridgeProjectStore and never
runs source-sync. It stops before the browser step and prints the exact next
handoff (claim -> stage -> preflight -> Send/cleanup -> native read -> capture)
so the browser seam stays an explicit, verifiable human/agent action.
"""

from __future__ import annotations

import argparse
import datetime as dt
import re
import subprocess
import sys
from pathlib import Path


SKILLS_ROOT = Path(__file__).resolve().parents[2]
BUNDLE_SCRIPT = SKILLS_ROOT / "bundle-algorithm-context" / "scripts" / "build_algorithm_bundle.py"
NOTES_SCRIPT = SKILLS_ROOT / "bundle-algorithm-context" / "scripts" / "prepare_codex_session_notes.py"
QW_SCRIPTS = SKILLS_ROOT / "gpt-pro-question-window" / "scripts"

VALID_ID = re.compile(r"^[a-z0-9](?:[a-z0-9-]{0,78}[a-z0-9])?$")


def slug(value: str, fallback: str = "probe") -> str:
    out = re.sub(r"[^a-z0-9]+", "-", (value or "").lower()).strip("-")
    return out or fallback


def build_probe_thread_id(track: str, candidate: str, probe_slug: str, date: str) -> str:
    parts = [slug(p) for p in (track, candidate, probe_slug, date) if slug(p) not in ("", "probe")]
    if not parts:
        parts = [slug(probe_slug)]
    thread_id = "-".join(parts)
    thread_id = re.sub(r"-+", "-", thread_id).strip("-")[:80].strip("-")
    if not VALID_ID.fullmatch(thread_id):
        raise SystemExit(f"Derived thread id is invalid: {thread_id!r}")
    return thread_id


def run(cmd: list[str]) -> str:
    result = subprocess.run(cmd, text=True, capture_output=True)
    if result.returncode != 0:
        sys.stderr.write(result.stderr)
        raise SystemExit(f"Command failed ({result.returncode}): {' '.join(cmd)}")
    if result.stderr.strip():
        sys.stderr.write(result.stderr)
    return result.stdout.strip()


def main() -> int:
    parser = argparse.ArgumentParser(description="Open a standalone GPT Pro Review Probe.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--track", default="", help="Research track/line label, e.g. m-ai.")
    parser.add_argument("--candidate", default="", help="Candidate/idea ref, e.g. c14. Opaque to Bridge.")
    parser.add_argument("--probe-slug", required=True, help="Short atomic-task slug, e.g. reward-leakage.")
    parser.add_argument("--date", default="", help="YYYYMMDD; defaults to today (local).")
    parser.add_argument("--bridge-thread-id", default="", help="Override the derived probe thread id.")
    parser.add_argument("--goal", required=True, help="What this probe asks GPT Pro to review.")
    parser.add_argument("--question", default="", help="Focused question for GPT Pro.")
    parser.add_argument("--summary", default="", help="Codex-side summary for the snapshot notes.")
    parser.add_argument("--summary-file", default="")
    parser.add_argument("--mode", default="algorithm_review",
                        help="Bundle mode: algorithm_review, paper_brainstorm, experiment_analysis, general_question, implementation_check.")
    parser.add_argument("--repo-context", default="auto", choices=["auto", "explicit", "none"])
    parser.add_argument("--include", nargs="*", default=[], help="Explicit files/dirs/globs for the bundle.")
    parser.add_argument("--max-files", type=int, default=24)
    args = parser.parse_args()

    repo = Path(args.repo).resolve()
    if not repo.is_dir():
        raise SystemExit(f"Repository root is not a directory: {repo}")

    date = args.date.strip() or dt.datetime.now().astimezone().strftime("%Y%m%d")
    thread_id = args.bridge_thread_id.strip() or build_probe_thread_id(
        args.track, args.candidate, args.probe_slug, date
    )
    summary = args.summary
    if args.summary_file:
        summary = Path(args.summary_file).read_text(encoding="utf-8").strip()
    if not summary:
        summary = args.goal

    # 1. Immutable Codex snapshot (standalone: no --bridge-project-id passed).
    notes_cmd = [
        sys.executable, str(NOTES_SCRIPT),
        "--repo", str(repo),
        "--bridge-thread-id", thread_id,
        "--goal", args.goal,
        "--summary", summary,
    ]
    if args.question:
        notes_cmd += ["--gpt-pro-question", args.question]
    notes_path = run(notes_cmd)

    # 2. Standalone scoped bundle: --standalone skips ALL Project source-sync
    #    gating, so a stale shared source never blocks this probe.
    bundle_cmd = [
        sys.executable, str(BUNDLE_SCRIPT),
        "--repo", str(repo),
        "--bridge-thread-id", thread_id,
        "--standalone",
        "--goal", args.goal,
        "--mode", args.mode,
        "--repo-context", args.repo_context,
        "--max-files", str(args.max_files),
    ]
    if args.question:
        bundle_cmd += ["--question", args.question]
    if args.include:
        bundle_cmd += ["--include", *args.include]
    bundle_path = run(bundle_cmd).splitlines()[-1].strip()

    claim_script = QW_SCRIPTS / "manage_browser_lease.py"
    staging_script = QW_SCRIPTS / "manage_browser_staging.py"
    preflight_script = QW_SCRIPTS / "check_browser_preflight.py"
    recovery_script = QW_SCRIPTS / "check_browser_recovery.py"
    save_script = QW_SCRIPTS / "save_bridge_turn.py"

    print(f"probe_thread_id: {thread_id}")
    print(f"codex_notes: {notes_path}")
    print(f"bundle: {bundle_path}")
    print()
    print("Next (different conversations may use separate owned tabs):", file=sys.stderr)
    print(f"  1. If a reserved chat id exists, run {claim_script} --repo {repo} acquire", file=sys.stderr)
    print(f"       --holder <unique-worker-id> --bridge-thread-id {thread_id} \\", file=sys.stderr)
    print("       --expected-conversation-id <reserved-chat-id>", file=sys.stderr)
    print("     Otherwise acquire the one-time new-chat bootstrap:", file=sys.stderr)
    print(f"       python3 {claim_script} --repo {repo} acquire \\", file=sys.stderr)
    print(f"       --holder <unique-worker-id> --bridge-thread-id {thread_id} \\", file=sys.stderr)
    print("       --scope profile --bootstrap", file=sys.stderr)
    print("     Bind the returned tab_owner_token only to that exact destination tab.", file=sys.stderr)
    print(f"  2. python3 {staging_script} stage --source {bundle_path} \\", file=sys.stderr)
    print(f"       --bridge-thread-id {thread_id}", file=sys.stderr)
    print("     Upload only staged_windows_path through DevTools MCP.", file=sys.stderr)
    print(f"  3. python3 {preflight_script} --repo {repo} \\", file=sys.stderr)
    print(f"       --bridge-thread-id {thread_id} --browser-lease-token <token> \\", file=sys.stderr)
    print("       --source-bundle <source> --bundle <staged-wsl-path> \\", file=sys.stderr)
    print("       --mcp-host-os windows --browser-host-os windows \\", file=sys.stderr)
    print("       --mcp-temp-root '<configured-browser-staging-root>' \\", file=sys.stderr)
    print("       --observed-page-url <url> --matching-page-count 1 \\", file=sys.stderr)
    print("       --observed-page-id <id> \\", file=sys.stderr)
    print("       --snapshot-page-id <id> --observed-tab-owner-token <owner> \\", file=sys.stderr)
    print("       --requested-model <checked-item> --selected-ui-label <same-item> \\", file=sys.stderr)
    print("       --model-selection-kind <exact-or-latest-alias> \\", file=sys.stderr)
    print("       --requested-thinking-intensity <complete-visible-value> \\", file=sys.stderr)
    print("       --selected-thinking-intensity <same-visible-value> \\", file=sys.stderr)
    print("       --attachment-name <visible-name> --upload-control <route> \\", file=sys.stderr)
    print("       [--expected-conversation-id <id> --observed-conversation-id <id> | \\", file=sys.stderr)
    print("        --conversation-bootstrap] \\", file=sys.stderr)
    print("       --pre-submit-boundary <cursor-or-turn-id-or-new-conversation> \\", file=sys.stderr)
    print("       --prompt-sha256 <sha256>", file=sys.stderr)
    print("  4. 按 attempt_recovery.md 保存预检/问题并创建 attempt；写入 send-started 后只发送一次。For bootstrap,", file=sys.stderr)
    print(f"     run {claim_script} --repo {repo} promote-bootstrap with the same", file=sys.stderr)
    print("     token/owner/page and exact post-Send conversation URL. Then release", file=sys.stderr)
    print("     with --staged-path, --staged-sha256, and", file=sys.stderr)
    print("     --terminal-state send-accepted. Do not retain the claim during generation.", file=sys.stderr)
    print("  5. 用 manage_bridge_attempt.py wait-plan 选择当前可用的原生或浏览器读取；pin the", file=sys.stderr)
    print("     matched completed, untruncated remote turn id.", file=sys.stderr)
    print(f"  6. python3 {save_script} --repo {repo} --bridge-thread-id {thread_id} \\", file=sys.stderr)
    print(f"       --standalone --single-round --bundle {bundle_path} \\", file=sys.stderr)
    print("       --web-url <conversation-url> --expected-conversation-id <id> \\", file=sys.stderr)
    print("       --capture-route <actual-capture-route> --remote-turn-id <turn-id> \\", file=sys.stderr)
    print("       --attempt-id <attempt-id> --response-completed-at <observed-ISO-8601> \\", file=sys.stderr)
    print("       --prompt-file <prompt> --answer-file <full-answer>", file=sys.stderr)
    print(f"  7. After reload, run {recovery_script} with fresh page/tab observations.", file=sys.stderr)
    print("     For browser fallback, also pass those observations to save_bridge_turn;", file=sys.stderr)
    print("     never capture or resend whichever turn merely appears latest.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
