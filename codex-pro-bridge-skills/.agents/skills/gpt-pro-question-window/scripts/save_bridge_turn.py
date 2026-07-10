#!/usr/bin/env python3
"""Capture one GPT Pro exchange and optionally record an immediate verdict."""

from __future__ import annotations

import argparse
import datetime as dt
import hashlib
import json
import re
import sys
from pathlib import Path
from urllib.parse import urlparse


SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import (  # noqa: E402
    BridgeError,
    append_event,
    atomic_write_text,
    bridge_root,
    default_codex_session_id,
    default_gpt_session_id,
    file_lock,
    file_sha256,
    now_iso,
    parse_metadata,
    record_codex_verdict,
    repo_relative,
    resolve_repo_path,
    validate_id,
    write_bound_metadata,
    write_session_index,
)


def read_value(text: str, file_path: str) -> str:
    if file_path:
        return Path(file_path).read_text(encoding="utf-8").strip()
    return (text or "").strip()


def slugify(value: str, fallback: str = "turn") -> str:
    slug = re.sub(r"[^a-zA-Z0-9]+", "-", value.strip().lower()).strip("-")
    return (slug or fallback)[:80].strip("-") or fallback


def one_line(value: str, limit: int = 180) -> str:
    text = re.sub(r"\s+", " ", value or "").strip()
    return text[: limit - 3].rstrip() + "..." if len(text) > limit else text or "-"


def next_turn_number(session_dir: Path) -> int:
    numbers = []
    for path in session_dir.glob("*.md"):
        match = re.match(r"^(\d+)-", path.name)
        if match:
            numbers.append(int(match.group(1)))
    return max(numbers, default=0) + 1


def validate_web_url(value: str) -> str:
    if not value:
        return ""
    parsed = urlparse(value)
    if parsed.scheme != "https" or not parsed.netloc:
        raise BridgeError("GPT Pro conversation URL must be an https URL")
    hostname = (parsed.hostname or "").lower()
    allowed = hostname in {"chatgpt.com", "chat.openai.com"} or hostname.endswith(
        (".chatgpt.com", ".chat.openai.com")
    )
    if not allowed:
        raise BridgeError("GPT Pro URL must point to a ChatGPT conversation")
    return value


def validate_timestamp(value: str, flag: str, *, default_now: bool = False) -> str:
    if not value:
        return now_iso() if default_now else ""
    try:
        parsed = dt.datetime.fromisoformat(value)
    except ValueError as exc:
        raise BridgeError(f"{flag} must be an ISO-8601 timestamp") from exc
    if parsed.tzinfo is None:
        raise BridgeError(f"{flag} must include a timezone offset")
    return value


def timestamp_value(value: str) -> dt.datetime | None:
    return dt.datetime.fromisoformat(value) if value else None


def build_turn(
    *,
    number: int,
    title: str,
    thread_id: str,
    gpt_session_id: str,
    codex_session_id: str,
    web_url: str,
    web_title: str,
    codex_notes: str,
    bundle_path: str,
    bundle_sha256: str,
    submitted_at: str,
    generation_observed_at: str,
    response_completed_at: str,
    response_wait_seconds: int | None,
    requested_model: str,
    selected_ui_label: str,
    model_verification: str,
    attachment_name: str,
    attachment_verification: str,
    upload_control: str,
    saved_at: str,
    prompt: str,
    answer: str,
    capture_summary: str,
    capture_fingerprint: str,
) -> str:
    number_text = f"{number:03d}"
    return "\n".join(
        [
            f"# {number_text} {title}",
            "",
            "## Metadata",
            f"- Bridge Thread ID: `{thread_id}`",
            f"- GPT Pro Session ID: `{gpt_session_id}`",
            f"- Codex Session ID: `{codex_session_id}`",
            f"- GPT Pro URL: {web_url}",
            f"- Web Title: {web_title or '-'}",
            f"- Codex Notes: {codex_notes or '-'}",
            f"- Bundle: {bundle_path or '-'}",
            f"- Bundle SHA-256: {bundle_sha256 or '-'}",
            f"- Capture Fingerprint: {capture_fingerprint}",
            f"- Requested Model: {requested_model or '-'}",
            f"- Selected UI Label: {selected_ui_label or '-'}",
            f"- Model Verification: {model_verification}",
            f"- Attachment Name: {attachment_name or '-'}",
            f"- Attachment Verification: {attachment_verification}",
            f"- Upload Control: {upload_control or '-'}",
            f"- Submitted at: {submitted_at}",
            f"- Generation observed at: {generation_observed_at or '-'}",
            f"- Response completed at: {response_completed_at or '-'}",
            f"- Response wait seconds: {response_wait_seconds if response_wait_seconds is not None else '-'}",
            f"- Captured at: {saved_at}",
            "",
            "## Prompt",
            "",
            prompt,
            "",
            "## Evidence Contract",
            "",
            f"- Bundle: {bundle_path or '-'}",
            f"- Bundle SHA-256: {bundle_sha256 or '-'}",
            f"- Codex notes: {codex_notes or '-'}",
            "- GPT Pro saw only the prompt and attached or pasted evidence.",
            "- The local repository remains the source of truth.",
            "",
            "## GPT Pro Answer",
            "",
            answer,
            "",
            "## Capture Summary",
            "",
            capture_summary or "_Pending Codex verdict._",
            "",
            "_Codex verification is stored as a later immutable verdict artifact and timeline event._",
            "",
        ]
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="Capture a GPT Pro answer as an immutable exchange.")
    parser.add_argument("--repo", default=".")
    parser.add_argument("--bridge-thread-id", required=True, help="Canonical task id.")
    parser.add_argument("--codex-session-id", default="", help="Defaults to <bridge-thread-id>-codex.")
    parser.add_argument("--codex-notes", default="", help="Defaults to the current Codex notes for this thread.")
    parser.add_argument("--gpt-pro-session-id", "--session-id", dest="gpt_pro_session_id", default="", help="Defaults to <bridge-thread-id>-gpt-pro.")
    parser.add_argument("--web-url", default="", help="Required when creating a GPT Pro session.")
    parser.add_argument("--web-title", default="")
    parser.add_argument("--purpose", default="")
    parser.add_argument("--turn-title", default="")
    parser.add_argument("--bundle", default="", help="Existing bundle under the repository root that was actually sent.")
    parser.add_argument(
        "--asked-at",
        "--submitted-at",
        dest="submitted_at",
        default="",
        help="Observed submission time as ISO-8601 with timezone.",
    )
    parser.add_argument("--generation-observed-at", default="", help="First observed generating state.")
    parser.add_argument("--response-completed-at", default="", help="Observed completed response time.")
    parser.add_argument("--requested-model", default="", help="Exact model label required by the task.")
    parser.add_argument("--selected-ui-label", default="", help="Exact selected model label visible before submission.")
    parser.add_argument("--attachment-name", default="", help="Attachment name visible in the composer before submission.")
    parser.add_argument("--upload-control", default="", help="Successful semantic upload route, for example visible-menu.")
    parser.add_argument("--prompt", default="")
    parser.add_argument("--prompt-file", default="")
    parser.add_argument("--answer", default="")
    parser.add_argument("--answer-file", default="")
    parser.add_argument("--summary", default="", help="Optional capture summary; also used by legacy immediate-verdict calls.")
    parser.add_argument("--summary-file", default="")
    parser.add_argument("--verification", default="", help="Compatibility path: record an immediate Codex verdict after capture.")
    parser.add_argument("--verification-file", default="")
    parser.add_argument("--decision-trail", default="")
    parser.add_argument("--decision-trail-file", default="")
    args = parser.parse_args()

    try:
        repo = Path(args.repo).resolve()
        if not repo.is_dir():
            raise BridgeError(f"Repository root is not a directory: {repo}")
        thread_id = validate_id(args.bridge_thread_id, "bridge thread id")
        codex_session_id = validate_id(
            args.codex_session_id or default_codex_session_id(thread_id), "Codex session id"
        )
        gpt_session_id = validate_id(
            args.gpt_pro_session_id or default_gpt_session_id(thread_id), "GPT Pro session id"
        )
        prompt = read_value(args.prompt, args.prompt_file)
        answer = read_value(args.answer, args.answer_file)
        summary = read_value(args.summary, args.summary_file)
        verification = read_value(args.verification, args.verification_file)
        decision_trail = read_value(args.decision_trail, args.decision_trail_file)
        if not prompt or not answer:
            raise BridgeError("Both prompt and full GPT Pro answer are required")
        if decision_trail and not verification:
            raise BridgeError("An immediate decision trail requires Codex verification")
        submitted_at = validate_timestamp(
            args.submitted_at, "--submitted-at", default_now=True
        )
        generation_observed_at = validate_timestamp(
            args.generation_observed_at, "--generation-observed-at"
        )
        response_completed_at = validate_timestamp(
            args.response_completed_at, "--response-completed-at"
        )
        saved_at = now_iso()
        submitted_value = timestamp_value(submitted_at)
        generation_value = timestamp_value(generation_observed_at)
        completed_value = timestamp_value(response_completed_at)
        if generation_value and submitted_value and generation_value < submitted_value:
            raise BridgeError("--generation-observed-at cannot precede --submitted-at")
        if completed_value and submitted_value and completed_value < submitted_value:
            raise BridgeError("--response-completed-at cannot precede --submitted-at")
        if completed_value and generation_value and completed_value < generation_value:
            raise BridgeError("--response-completed-at cannot precede --generation-observed-at")
        response_wait_seconds = (
            int((completed_value - submitted_value).total_seconds())
            if completed_value and submitted_value
            else None
        )
        requested_model = args.requested_model.strip()
        selected_ui_label = args.selected_ui_label.strip()
        model_verification = (
            "verified"
            if requested_model and selected_ui_label and requested_model == selected_ui_label
            else "mismatch"
            if requested_model and selected_ui_label
            else "unverified"
        )

        bridge_dir = bridge_root(repo)
        codex_meta = parse_metadata(
            bridge_dir / "codex-sessions" / codex_session_id / "session.md"
        )
        if not codex_meta:
            raise BridgeError(
                "Codex session metadata is required; run prepare_codex_session_notes.py first"
            )
        if codex_meta.get("bridge_thread_id") not in (None, "", thread_id):
            raise BridgeError(
                f"Codex session {codex_session_id} is bound to "
                f"{codex_meta['bridge_thread_id']}, not {thread_id}"
            )
        sessions_dir = bridge_dir / "gpt-pro-sessions"
        session_dir = sessions_dir / gpt_session_id
        session_dir.mkdir(parents=True, exist_ok=True)
        session_file = session_dir / "session.md"
        previous = parse_metadata(session_file)
        if previous.get("bridge_thread_id") not in (None, "", thread_id):
            raise BridgeError(
                f"GPT Pro session {gpt_session_id} is already bound to "
                f"{previous['bridge_thread_id']}; refusing to move it to {thread_id}"
            )
        if previous.get("codex_session_id") not in (None, "", codex_session_id):
            raise BridgeError(
                f"GPT Pro session {gpt_session_id} is already linked to Codex session "
                f"{previous['codex_session_id']}"
            )
        web_url = validate_web_url(args.web_url or previous.get("web_conversation_url", ""))
        if not web_url:
            raise BridgeError("--web-url is required when creating a GPT Pro session")
        if previous.get("web_conversation_url") not in (None, "", web_url):
            raise BridgeError("A GPT Pro session cannot be rebound to another web conversation URL")

        notes_path = (
            resolve_repo_path(args.codex_notes, repo)
            if args.codex_notes
            else resolve_repo_path(codex_meta["latest_snapshot"], repo)
            if codex_meta.get("latest_snapshot")
            else bridge_dir / "codex-sessions" / codex_session_id / "notes.md"
        )
        if not notes_path.is_file():
            raise BridgeError("The immutable Codex notes snapshot is missing")
        codex_notes = repo_relative(notes_path, repo)
        bundle_path = ""
        bundle_sha256 = ""
        if args.bundle:
            bundle = resolve_repo_path(args.bundle, repo)
            if not bundle.is_file():
                raise BridgeError("--bundle must point to an existing file")
            bundle_path = repo_relative(bundle, repo)
            bundle_sha256 = file_sha256(bundle)
        attachment_name = args.attachment_name.strip()
        attachment_verification = (
            "verified"
            if bundle_path and attachment_name == Path(bundle_path).name
            else "mismatch"
            if bundle_path and attachment_name
            else "not-required"
            if not bundle_path
            else "unverified"
        )

        title = one_line(args.turn_title or args.web_title or args.purpose or gpt_session_id, 120)
        capture_fingerprint = hashlib.sha256(
            json.dumps(
                {
                    "thread_id": thread_id,
                    "gpt_session_id": gpt_session_id,
                    "web_url": web_url,
                    "prompt": prompt,
                    "answer": answer,
                    "bundle_sha256": bundle_sha256,
                    "requested_model": requested_model,
                    "selected_ui_label": selected_ui_label,
                    "model_verification": model_verification,
                    "attachment_name": attachment_name,
                    "attachment_verification": attachment_verification,
                    "upload_control": args.upload_control.strip(),
                    "submitted_at": submitted_at,
                    "generation_observed_at": generation_observed_at,
                    "response_completed_at": response_completed_at,
                },
                ensure_ascii=False,
                sort_keys=True,
            ).encode("utf-8")
        ).hexdigest()
        with file_lock(session_dir / ".session.lock"):
            turn_file = next(
                (
                    path
                    for path in sorted(session_dir.glob("*.md"))
                    if f"- Capture Fingerprint: {capture_fingerprint}" in path.read_text(
                        encoding="utf-8"
                    )
                ),
                None,
            )
            if turn_file is None:
                number = next_turn_number(session_dir)
                turn_file = session_dir / f"{number:03d}-{slugify(title)}.md"
                if turn_file.exists():
                    raise BridgeError(f"Refusing to overwrite turn: {turn_file}")
                atomic_write_text(
                    turn_file,
                    build_turn(
                        number=number,
                        title=title,
                        thread_id=thread_id,
                        gpt_session_id=gpt_session_id,
                        codex_session_id=codex_session_id,
                        web_url=web_url,
                        web_title=args.web_title or previous.get("web_title", ""),
                        codex_notes=codex_notes,
                        bundle_path=bundle_path,
                        bundle_sha256=bundle_sha256,
                        submitted_at=submitted_at,
                        generation_observed_at=generation_observed_at,
                        response_completed_at=response_completed_at,
                        response_wait_seconds=response_wait_seconds,
                        requested_model=requested_model,
                        selected_ui_label=selected_ui_label,
                        model_verification=model_verification,
                        attachment_name=attachment_name,
                        attachment_verification=attachment_verification,
                        upload_control=args.upload_control.strip(),
                        saved_at=saved_at,
                        prompt=prompt,
                        answer=answer,
                        capture_summary=summary,
                        capture_fingerprint=capture_fingerprint,
                    ),
                )
            else:
                match = re.match(r"^(\d+)-", turn_file.name)
                if not match:
                    raise BridgeError(f"Cannot recover turn number from {turn_file}")
                number = int(match.group(1))
            write_bound_metadata(
                session_file,
                {
                    "gpt_pro_session_id": gpt_session_id,
                    "bridge_thread_id": thread_id,
                    "codex_session_id": codex_session_id,
                    "web_conversation_url": web_url,
                    "web_title": args.web_title or previous.get("web_title", "") or title,
                    "purpose": args.purpose or previous.get("purpose", "") or title,
                    "created_at": previous.get("created_at", "") or saved_at,
                    "last_used_at": saved_at,
                    "latest_turn": f"{number:03d}",
                },
                ordered_keys=(
                    "gpt_pro_session_id",
                    "bridge_thread_id",
                    "codex_session_id",
                    "web_conversation_url",
                    "web_title",
                    "purpose",
                    "created_at",
                    "last_used_at",
                    "latest_turn",
                ),
                immutable_keys=(
                    "gpt_pro_session_id",
                    "bridge_thread_id",
                    "codex_session_id",
                    "web_conversation_url",
                    "created_at",
                ),
            )
        write_session_index(sessions_dir, kind="gpt-pro")
        turn_rel = repo_relative(turn_file, repo)
        append_event(
            repo,
            thread_id=thread_id,
            event_type="gpt-exchange",
            actor="gpt-pro",
            thread_title=args.purpose or args.web_title or title,
            codex_session_id=codex_session_id,
            gpt_pro_session_id=gpt_session_id,
            artifact={"kind": "gpt-pro-turn", "path": turn_rel, "sha256": file_sha256(turn_file)},
            data={
                "turn": turn_rel,
                "question": one_line(prompt),
                "summary": one_line(summary),
                "bundle": bundle_path,
                "bundle_sha256": bundle_sha256,
                "web_title": args.web_title or previous.get("web_title", "") or title,
                "requested_model": requested_model,
                "selected_ui_label": selected_ui_label,
                "model_verification": model_verification,
                "attachment_name": attachment_name,
                "attachment_verification": attachment_verification,
                "upload_control": args.upload_control.strip(),
                "submitted_at": submitted_at,
                "generation_observed_at": generation_observed_at,
                "response_completed_at": response_completed_at,
                "response_wait_seconds": response_wait_seconds,
            },
            dedupe_key=f"gpt-exchange:{capture_fingerprint}",
            occurred_at=saved_at,
        )

        if verification or decision_trail:
            verdict_path = record_codex_verdict(
                repo,
                thread_id=thread_id,
                gpt_pro_session_id=gpt_session_id,
                codex_session_id=codex_session_id,
                turn_path=turn_file,
                summary=summary,
                verification=verification,
                decision_trail=decision_trail,
            )
            print(f"Immediate verdict: {verdict_path}", file=sys.stderr)
        print(turn_file)
        return 0
    except (BridgeError, OSError) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
