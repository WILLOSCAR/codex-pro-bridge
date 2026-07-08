#!/usr/bin/env python3
"""Build a compact direct-evidence algorithm-context bundle for GPT Pro review.

This script is intentionally dependency-free. Run it from a repository root.
"""

from __future__ import annotations

import argparse
import datetime as dt
import fnmatch
import json
import os
import re
import subprocess
import sys
import zipfile
from pathlib import Path
from typing import Dict, Iterable, List, Tuple

DEFAULT_INCLUDE_EXTS = {
    ".py", ".ipynb", ".md", ".txt", ".rst", ".yaml", ".yml", ".json", ".jsonl",
    ".toml", ".ini", ".cfg", ".sh", ".bash", ".zsh", ".sql", ".ts", ".tsx",
    ".js", ".jsx", ".html", ".css", ".scss", ".less", ".go", ".rs", ".java", ".scala", ".kt", ".cpp", ".cc", ".c",
    ".h", ".hpp", ".cu", ".m", ".mm", ".swift", ".R", ".jl", ".log",
}

STATIC_OR_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tar",
    ".gz", ".bz2", ".xz", ".7z", ".mp4", ".mov", ".avi", ".mp3", ".wav",
    ".parquet", ".feather", ".arrow", ".pt", ".pth", ".ckpt", ".onnx", ".bin",
}

DEFAULT_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", "node_modules", "dist", "build",
    "target", "out", "coverage", "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", ".venv", "venv", "env", ".env", "site-packages", ".next", ".turbo",
    ".codex", "wandb", "mlruns", "checkpoints", "outputs", "runs", "artifacts", "data", "datasets",
}

SECRET_NAME_PATTERNS = [
    "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", ".env*", "*.secret*",
    "*credential*", "*credentials*", "*cookie*", "*cookies*", "*.sqlite", "*.db",
    ".npmrc", ".pypirc", "netrc", ".netrc",
]

ALGORITHM_HINTS = [
    "train", "trainer", "loss", "reward", "rm", "urm", "dpo", "ppo", "grpo", "opd",
    "eval", "evaluate", "metric", "judge", "dataset", "data", "label", "sampler", "sample",
    "rollout", "agent", "tool", "retrieval", "rank", "ranking", "pipeline", "config", "experiment",
    "ablation", "baseline", "model", "inference", "prompt", "rubric", "preprocess", "postprocess",
]

GRAPH_EVENT_LIMIT = 40
GRAPH_LABELS = {
    "codex-update": "codex",
    "bundle": "bundle",
    "gpt-pro-turn": "gpt",
}


def run(cmd: List[str], cwd: Path) -> str:
    try:
        return subprocess.check_output(cmd, cwd=str(cwd), stderr=subprocess.DEVNULL, text=True).strip()
    except Exception:
        return ""


def is_git_repo(root: Path) -> bool:
    return bool(run(["git", "rev-parse", "--is-inside-work-tree"], root))


def git_files(root: Path) -> List[Path]:
    output = run(["git", "ls-files"], root)
    return [root / line for line in output.splitlines() if line.strip()]


def walk_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [d for d in dirnames if d not in DEFAULT_EXCLUDE_DIRS]
        for name in filenames:
            files.append(Path(dirpath) / name)
    return files


def rel(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except Exception:
        return path.as_posix()


def yaml_quote(value: str) -> str:
    return json.dumps(value or "", ensure_ascii=False)


def parse_metadata(path: Path) -> Dict[str, str]:
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


def extract_timeline(text: str) -> str:
    marker = "## Timeline\n"
    if marker not in text:
        return ""
    return text.split(marker, 1)[1].strip()


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


def format_timeline_event(event: Dict[str, object]) -> str:
    timestamp = str(event.get("timestamp", "")).strip()
    event_type = str(event.get("event_type", "event")).strip()
    lines = [str(line) for line in event.get("lines", [])]
    header = f"### {timestamp} - {event_type}" if timestamp else f"### {event_type}"
    return "\n".join([header, "", *lines]).strip()


def graph_label(index: int, event_type: str) -> str:
    label = GRAPH_LABELS.get(event_type, slugify(event_type, "event")[:8])
    return f"{index:02d} {label}"


def build_git_graph_from_events(events: List[Dict[str, object]], start_index: int) -> str:
    selected = events[-GRAPH_EVENT_LIMIT:]
    start_index = start_index + max(0, len(events) - len(selected))
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
    return "\n".join(lines)


def compact_thread_context(thread_file: Path, root: Path, max_events: int, max_chars: int) -> str:
    if not thread_file.exists() or not thread_file.is_file():
        return "_No prior bridge thread timeline was available._"

    text = thread_file.read_text(encoding="utf-8")
    meta = parse_metadata(thread_file)
    timeline = extract_timeline(text)
    events = parse_timeline_events(timeline)
    max_events = max(1, max_events)
    selected = events[-max_events:]
    start_index = max(1, len(events) - len(selected) + 1)

    def render(events_to_render: List[Dict[str, object]], first_index: int) -> str:
        omitted = len(events) - len(events_to_render)
        lines = [
            f"- Full thread file: `{display_path(thread_file, root)}`",
            f"- Bridge thread id: `{meta.get('bridge_thread_id', thread_file.stem)}`",
            f"- Title: {meta.get('title', '-')}",
            f"- Total events: {len(events)}",
            f"- Included events: latest {len(events_to_render)}",
            f"- Older events omitted from bundle: {max(0, omitted)}",
            "",
            "### Recent Graph",
            build_git_graph_from_events(events_to_render, first_index),
            "",
            "### Recent Events",
        ]
        if not events_to_render:
            lines.append("_No timeline events recorded yet._")
        else:
            lines.extend(format_timeline_event(event) for event in events_to_render)
        return "\n\n".join(lines).strip()

    content = render(selected, start_index)
    while len(content) > max_chars and len(selected) > 1:
        selected = selected[1:]
        start_index += 1
        content = render(selected, start_index)
    if len(content) > max_chars:
        content = content[: max(0, max_chars - 120)].rstrip() + "\n\n_Thread context truncated to bundle budget._"
    return content


def is_excluded_by_name(rel_path: str) -> bool:
    parts = Path(rel_path).parts
    if any(part in DEFAULT_EXCLUDE_DIRS for part in parts):
        return True
    base = os.path.basename(rel_path)
    return any(fnmatch.fnmatch(base, pat) or fnmatch.fnmatch(rel_path, pat) for pat in SECRET_NAME_PATTERNS)


def is_candidate(path: Path, root: Path, include_log: bool) -> bool:
    rp = rel(path, root)
    if is_excluded_by_name(rp):
        return False
    ext = path.suffix.lower()
    if ext in STATIC_OR_BINARY_EXTS:
        return False
    if ext == ".log" and not include_log:
        return False
    return ext in DEFAULT_INCLUDE_EXTS or path.name in {"README", "AGENTS", "Makefile", "Dockerfile"}


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def read_text(path: Path, max_bytes: int) -> Tuple[str, bool]:
    try:
        data = path.read_bytes()
    except Exception as exc:
        return f"<UNREADABLE: {exc}>", False
    truncated = len(data) > max_bytes
    data = data[:max_bytes]
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        try:
            text = data.decode("utf-8", errors="replace")
        except Exception:
            return "<BINARY_OR_UNDECODABLE>", truncated
    return text, truncated


def keywords_from_text(*texts: str) -> List[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_\-]{2,}", "\n".join(texts).lower())
    stop = {"the", "and", "for", "with", "this", "that", "from", "into", "what", "when", "where", "which", "about", "should", "could", "would", "please", "need"}
    result = []
    for w in words:
        if w not in stop and w not in result:
            result.append(w)
    return result[:80]


def score_file(path: Path, root: Path, keywords: Iterable[str], changed: set[str]) -> int:
    rp = rel(path, root).lower()
    name = path.name.lower()
    score = 0
    if rel(path, root) in changed:
        score += 12
    if name in {"readme.md", "agents.md", "pyproject.toml", "package.json", "requirements.txt", "environment.yml"}:
        score += 8
    for hint in ALGORITHM_HINTS:
        if hint in rp:
            score += 3
    for kw in keywords:
        if kw and kw in rp:
            score += 4
    if path.suffix.lower() in {".yaml", ".yml", ".toml", ".json", ".ini", ".cfg"}:
        score += 2
    if path.suffix.lower() in {".md", ".rst", ".txt"}:
        score += 2
    return score


def parse_include_paths(items: List[str], root: Path) -> List[Path]:
    paths: List[Path] = []
    for item in items:
        p = (root / item).resolve() if not os.path.isabs(item) else Path(item).resolve()
        if p.is_dir():
            for sub in p.rglob("*"):
                if sub.is_file():
                    paths.append(sub)
        elif p.exists():
            paths.append(p)
        else:
            # Preserve globs.
            matches = list(root.glob(item))
            paths.extend([m for m in matches if m.is_file()])
    # Stable dedupe.
    seen = set()
    deduped = []
    for p in paths:
        rp = str(p)
        if rp not in seen:
            seen.add(rp)
            deduped.append(p)
    return deduped


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


def display_path(path: Path, root: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def filter_files(
    paths: Iterable[Path],
    root: Path,
    include_log: bool,
    max_files: int,
    skip_files_over_bytes: int,
) -> Tuple[List[Path], List[Tuple[str, str]]]:
    selected: List[Path] = []
    omitted: List[Tuple[str, str]] = []
    for path in paths:
        rp = rel(path, root)
        if not path.is_file():
            omitted.append((rp, "not a file"))
            continue
        if not is_candidate(path, root, include_log):
            omitted.append((rp, "excluded by path, name, extension, or static/binary policy"))
            continue
        size = file_size(path)
        if skip_files_over_bytes > 0 and size > skip_files_over_bytes:
            omitted.append((rp, f"over size threshold ({size} bytes > {skip_files_over_bytes})"))
            continue
        if len(selected) >= max_files:
            omitted.append((rp, f"max file count reached ({max_files})"))
            continue
        selected.append(path)
    return selected, omitted


def markdown_code_fence(path: Path, content: str) -> str:
    ext = path.suffix.lower().lstrip(".") or "text"
    lang_map = {"py": "python", "md": "markdown", "yml": "yaml", "yaml": "yaml", "jsonl": "json", "sh": "bash", "bash": "bash", "zsh": "bash", "toml": "toml", "ini": "ini", "cfg": "ini", "ts": "typescript", "tsx": "tsx", "js": "javascript", "jsx": "jsx", "rs": "rust", "go": "go", "java": "java", "cpp": "cpp", "cc": "cpp", "c": "c", "h": "c", "hpp": "cpp", "sql": "sql", "log": "text", "txt": "text"}
    lang = lang_map.get(ext, ext)
    if "```" in content:
        content = content.replace("```", "``\u200b`")
    return f"```{lang}\n{content}\n```"


def write_zip_text(zf: zipfile.ZipFile, archive_name: str, content: str) -> None:
    zf.writestr(archive_name, content.encode("utf-8"))


def write_zip_file(zf: zipfile.ZipFile, source: Path, archive_name: str) -> None:
    zf.write(source, archive_name)


def write_thread_index(threads_dir: Path) -> None:
    rows: List[Dict[str, str]] = []
    for thread_file in threads_dir.glob("*.md"):
        if thread_file.name == "index.md":
            continue
        meta = parse_metadata(thread_file)
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
                (meta.get("bridge_thread_id", "") or "-").replace("|", "\\|"),
                (meta.get("title", "") or "-").replace("|", "\\|"),
                (meta.get("codex_session_ids", "") or "-").replace("|", "\\|"),
                (meta.get("gpt_pro_session_ids", "") or "-").replace("|", "\\|"),
                (meta.get("last_used_at", "") or "-").replace("|", "\\|"),
                (meta.get("latest_event", "") or "-").replace("|", "\\|"),
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
    previous = parse_metadata(thread_path)
    previous_text = thread_path.read_text(encoding="utf-8") if thread_path.exists() else ""
    timeline = ""
    marker = "## Timeline\n"
    if marker in previous_text:
        timeline = previous_text.split(marker, 1)[1].strip()

    event = "\n".join([f"### {now} - {event_type}", "", *details]).strip()
    timeline = "\n\n".join(part for part in [timeline, event] if part).strip()
    events = parse_timeline_events(timeline)
    graph = build_git_graph_from_events(events, 1)
    if len(events) > GRAPH_EVENT_LIMIT:
        graph = f"_Graph shows the latest {GRAPH_EVENT_LIMIT} of {len(events)} events._\n\n{graph}"
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


def main() -> int:
    parser = argparse.ArgumentParser(description="Build a GPT Pro algorithm review context bundle.")
    parser.add_argument("--repo", default=".", help="Repository root. Default: current directory.")
    parser.add_argument("--goal", required=True, help="User goal or task.")
    parser.add_argument("--question", default="", help="Specific question for GPT Pro.")
    parser.add_argument("--mode", default="algorithm_review", choices=["algorithm_review", "experiment_analysis", "paper_brainstorm", "implementation_check", "general_question"])
    parser.add_argument("--repo-context", default="auto", choices=["auto", "explicit", "none"], help="Repository file policy: auto-select files, include only explicit paths, or include no repository files.")
    parser.add_argument("--include", nargs="*", default=[], help="Explicit files, directories, or globs to include.")
    parser.add_argument("--max-files", type=int, default=24, help="Maximum number of files to include when auto-selecting.")
    parser.add_argument("--max-file-bytes", type=int, default=60_000, help="Max bytes read per file.")
    parser.add_argument("--max-total-chars", type=int, default=180_000, help="Soft max characters in final bundle.")
    parser.add_argument("--max-thread-events", type=int, default=24, help="Maximum recent bridge thread events to include in the bundle.")
    parser.add_argument("--max-thread-chars", type=int, default=20_000, help="Maximum characters of compact bridge thread context to include in the bundle.")
    parser.add_argument("--skip-files-over-bytes", type=int, default=250_000, help="Skip source/config/doc files larger than this size instead of truncating them.")
    parser.add_argument("--include-logs", action="store_true", help="Allow .log files to be auto-included.")
    parser.add_argument("--bridge-thread-id", default="", help="Task-level id that links Codex notes, bundles, and GPT Pro turns.")
    parser.add_argument("--codex-session-id", default="current-codex-session", help="Codex-side session id used to find required notes.")
    parser.add_argument("--codex-session-notes", default="", help="Required Codex-side session notes file. Default: .codex/codex-pro-bridge/codex-sessions/<id>/notes.md.")
    parser.add_argument("--allow-missing-codex-session-notes", action="store_true", help="Allow bundling without Codex session notes. Use only for exceptional one-off bundles.")
    parser.add_argument("--notes", default="", help="Deprecated alias for --codex-session-notes.")
    parser.add_argument("--extra-notes", nargs="*", default=[], help="Additional notes files to append after Codex session notes.")
    parser.add_argument("--format", default="markdown", choices=["markdown", "zip"], help="Bundle artifact format. Use zip for ChatGPT file upload with source files kept separate from the manifest.")
    parser.add_argument("--out", default="", help="Output path. Default under .codex/codex-pro-bridge/bundles/ with extension matching --format.")
    args = parser.parse_args()

    root = Path(args.repo).resolve()
    if not root.exists():
        print(f"Repo path does not exist: {root}", file=sys.stderr)
        return 2

    codex_session_id = slugify(args.codex_session_id, "current-codex-session")
    bridge_dir = root / ".codex" / "codex-pro-bridge"
    codex_session_meta = parse_metadata(bridge_dir / "codex-sessions" / codex_session_id / "session.md")
    bridge_thread_id = derive_bridge_thread_id(args.bridge_thread_id, codex_session_meta.get("bridge_thread_id", ""), codex_session_id)
    thread_file = bridge_dir / "threads" / f"{bridge_thread_id}.md"
    default_notes_path = root / ".codex" / "codex-pro-bridge" / "codex-sessions" / codex_session_id / "notes.md"
    notes_arg = args.codex_session_notes or args.notes
    codex_session_notes = (root / notes_arg).resolve() if notes_arg and not os.path.isabs(notes_arg) else Path(notes_arg).resolve() if notes_arg else default_notes_path
    if not codex_session_notes.exists() and not args.allow_missing_codex_session_notes:
        print(
            "Codex session notes are required. Create them first, for example:\n"
            "  python3 .agents/skills/bundle-algorithm-context/scripts/prepare_codex_session_notes.py \\\n"
            f"    --repo . --bridge-thread-id {bridge_thread_id} --codex-session-id {codex_session_id} --summary-file /tmp/codex-session-summary.md\n"
            "Then rerun this bundle command, or pass --allow-missing-codex-session-notes for an exceptional one-off bundle.",
            file=sys.stderr,
        )
        return 2

    now = dt.datetime.now().strftime("%Y%m%d-%H%M%S")
    default_ext = "zip" if args.format == "zip" else "md"
    out = Path(args.out) if args.out else root / ".codex" / "codex-pro-bridge" / "bundles" / f"{now}-{args.mode}-context.{default_ext}"
    if not out.is_absolute():
        out = root / out
    out = out.resolve()
    out.parent.mkdir(parents=True, exist_ok=True)

    git = is_git_repo(root)
    branch = run(["git", "branch", "--show-current"], root) if git else ""
    commit = run(["git", "rev-parse", "--short", "HEAD"], root) if git else ""
    status = run(["git", "status", "--short"], root) if git else ""
    diff_stat = run(["git", "diff", "--stat"], root) if git else ""
    changed = set()
    if git:
        for line in status.splitlines():
            candidate = line[3:].strip()
            if " -> " in candidate:
                candidate = candidate.split(" -> ")[-1]
            if candidate:
                changed.add(candidate)

    keywords = keywords_from_text(args.goal, args.question)
    explicit = parse_include_paths(args.include, root) if args.include else []
    omitted: List[Tuple[str, str]] = []
    if args.repo_context == "none":
        selected = []
        if explicit:
            omitted.extend((display_path(path, root), "repo context disabled by --repo-context none") for path in explicit)
    elif explicit:
        selected, omitted = filter_files(explicit, root, include_log=True, max_files=args.max_files, skip_files_over_bytes=args.skip_files_over_bytes)
    elif args.repo_context == "explicit":
        selected = []
    else:
        all_files = git_files(root) if git else walk_files(root)
        candidates = [p for p in all_files if p.is_file() and is_candidate(p, root, args.include_logs)]
        ranked = sorted(candidates, key=lambda p: (score_file(p, root, keywords, changed), -len(rel(p, root))), reverse=True)
        scored = [p for p in ranked if score_file(p, root, keywords, changed) > 0]
        selected, omitted = filter_files(scored, root, include_log=args.include_logs, max_files=args.max_files, skip_files_over_bytes=args.skip_files_over_bytes)

    extra_notes_paths = []
    for item in args.extra_notes:
        p = (root / item).resolve() if not os.path.isabs(item) else Path(item).resolve()
        if p.exists() and p.is_file():
            extra_notes_paths.append(p)
        else:
            omitted.append((item, "extra notes file not found"))

    sections: List[str] = []
    sections.append("# Algorithm Context Bundle")
    sections.append("")
    sections.append("## 1. Metadata")
    sections.append(f"- Generated: {dt.datetime.now().isoformat(timespec='seconds')}")
    sections.append(f"- Repo: `{root}`")
    if git:
        sections.append(f"- Git branch: `{branch or '<unknown>'}`")
        sections.append(f"- Git commit: `{commit or '<unknown>'}`")
    sections.append(f"- Mode: `{args.mode}`")
    sections.append(f"- Repository context: `{args.repo_context}`")
    sections.append(f"- Bridge thread id: `{bridge_thread_id}`")
    sections.append(f"- Codex session id: `{codex_session_id}`")
    if codex_session_notes.exists():
        sections.append(f"- Codex session notes: `{display_path(codex_session_notes, root)}`")
    else:
        sections.append("- Codex session notes: `<missing; explicitly allowed>`")
    sections.append("")
    sections.append("## 2. User Goal")
    sections.append(args.goal.strip())
    sections.append("")
    sections.append("## 3. Question for GPT Pro")
    sections.append(args.question.strip() or "Please perform the requested review based on the bundle.")
    sections.append("")
    sections.append("## 4. Review Instructions for GPT Pro")
    sections.append(
        "Treat this bundle as partial evidence. Do not assume access to files not included. "
        "Be adversarial and explicit about uncertainty. Separate algorithm issues, experiment/pipeline issues, and implementation issues. "
        "End with a go/no-go judgment and the minimal experiments that would most quickly validate or kill the idea."
    )
    sections.append("")
    sections.append("## 5. Evidence Boundary")
    sections.append("GPT Pro should treat only the following Codex-side notes and repository files as supplied evidence.")
    sections.append("")
    sections.append("### Codex-side session notes")
    if codex_session_notes.exists():
        sections.append(f"- `{display_path(codex_session_notes, root)}`")
    else:
        sections.append("- Missing. Bundling continued only because `--allow-missing-codex-session-notes` was passed.")
    sections.append("")
    sections.append("### Bridge thread timeline")
    if thread_file.exists():
        sections.append(f"- `{display_path(thread_file, root)}`")
    else:
        sections.append("- No prior bridge thread timeline was found.")
    sections.append("")
    if extra_notes_paths:
        sections.append("### Extra notes")
        for p in extra_notes_paths:
            sections.append(f"- `{display_path(p, root)}`")
        sections.append("")
    sections.append("### Repository files")
    if selected:
        for p in selected:
            sections.append(f"- `{rel(p, root)}`")
    else:
        sections.append("- No repository files were selected.")
        if args.repo_context == "explicit":
            sections.append("- Repository context is `explicit`; pass `--include` to attach code/config files for this round.")
        elif args.repo_context == "none":
            sections.append("- Repository context is `none`; this round intentionally uses notes and thread context only.")
    sections.append("")
    if omitted:
        sections.append("### Omitted files")
        for rp, reason in omitted[:80]:
            sections.append(f"- `{rp}`: {reason}")
        if len(omitted) > 80:
            sections.append(f"- ... {len(omitted) - 80} more omitted files")
        sections.append("")
    sections.append(
        "Files not listed above were not provided. Env files, credentials, cookies, private keys, "
        "databases, raw data dumps, large artifacts, vendor folders, and unrelated generated outputs "
        "are excluded by policy."
    )
    sections.append("")
    if git:
        sections.append("## 6. Git Status")
        sections.append(markdown_code_fence(Path("status.txt"), status or "<clean or unavailable>"))
        sections.append("")
        sections.append("## 7. Git Diff Stat")
        sections.append(markdown_code_fence(Path("diff_stat.txt"), diff_stat or "<no diff stat or unavailable>"))
        sections.append("")
    sections.append("## 8. Selected Files")
    if not selected:
        if args.repo_context == "explicit":
            sections.append("No repository files were included because `--repo-context explicit` was used without `--include` paths.")
        elif args.repo_context == "none":
            sections.append("No repository files were included because `--repo-context none` was used.")
        else:
            sections.append("No files were selected automatically. Re-run with explicit `--include` paths.")
    else:
        if args.format == "zip":
            sections.append("The following files are included as separate files under `source/` in this zip.")
        else:
            sections.append("The following files are included. They may be truncated per-file to keep the bundle manageable.")
        sections.append("")
        for p in selected:
            rp = rel(p, root)
            if args.format == "zip":
                sections.append(f"- `source/{rp}`")
            else:
                text, truncated = read_text(p, args.max_file_bytes)
                sections.append(f"### `{rp}`")
                if truncated:
                    sections.append(f"_Note: file truncated to first {args.max_file_bytes} bytes._")
                sections.append(markdown_code_fence(p, text))
                sections.append("")
                if sum(len(s) + 2 for s in sections) > args.max_total_chars:
                    sections.append("\n_Bundle soft character limit reached; remaining files omitted._")
                    break

    if args.format == "zip":
        sections.append("")
        sections.append("## 9. Context Files In This Zip")
        if codex_session_notes.exists() and codex_session_notes.is_file():
            sections.append("- `context/codex-session-notes.md`")
        else:
            sections.append("- Codex session notes were not available.")
        sections.append("- `context/bridge-thread-context.md`")
        if git:
            sections.append("- `context/git-status.txt`")
            sections.append("- `context/git-diff-stat.txt`")
        if extra_notes_paths:
            for notes_path in extra_notes_paths:
                sections.append(f"- `context/extra-notes/{notes_path.name}`")
        sections.append("")
    else:
        sections.append("## 9. Codex Session Notes")
        if codex_session_notes.exists() and codex_session_notes.is_file():
            notes, trunc = read_text(codex_session_notes, args.max_file_bytes)
            if trunc:
                sections.append(f"_Note: Codex session notes truncated to first {args.max_file_bytes} bytes._")
            sections.append(markdown_code_fence(codex_session_notes, notes))
        else:
            sections.append("_Codex session notes were not available._")
        sections.append("")

        sections.append("## 10. Bridge Thread Context")
        sections.append(
            "The local thread file remains the full ledger. This bundle includes a compact recent window "
            "so long multi-round conversations do not consume the whole context."
        )
        sections.append("")
        sections.append(
            compact_thread_context(
                thread_file=thread_file,
                root=root,
                max_events=args.max_thread_events,
                max_chars=args.max_thread_chars,
            )
        )
        sections.append("")

    if args.format == "markdown" and extra_notes_paths:
        sections.append("## 11. Extra Notes")
        for notes_path in extra_notes_paths:
            notes, trunc = read_text(notes_path, args.max_file_bytes)
            sections.append(f"### `{display_path(notes_path, root)}`")
            if trunc:
                sections.append(f"_Note: notes file truncated to first {args.max_file_bytes} bytes._")
            sections.append(markdown_code_fence(notes_path, notes))
            sections.append("")

    requested_output = (
        "Please return:\n"
        "1. Problem Restatement\n"
        "2. Core Hypothesis\n"
        "3. Method Decomposition\n"
        "4. Strongest Arguments For / Against\n"
        "5. Hidden Assumptions\n"
        "6. Baseline Gaps\n"
        "7. Data / Label / Leakage Risks\n"
        "8. Reward / Loss / Optimization Risks\n"
        "9. Evaluation Risks\n"
        "10. Ablation Matrix\n"
        "11. Implementation Checkpoints for Codex\n"
        "12. Paper Angle / Novelty, if relevant\n"
        "13. Reviewer Objections\n"
        "14. Minimal Experiment Plan\n"
        "15. Go / No-Go Judgment"
    )
    sections.append("## 12. Requested Output Format")
    sections.append(requested_output)

    content = "\n".join(sections).strip() + "\n"
    if args.format == "zip":
        with zipfile.ZipFile(out, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            write_zip_text(zf, "README_FOR_GPT_PRO.md", content)
            for p in selected:
                write_zip_file(zf, p, f"source/{rel(p, root)}")
            if codex_session_notes.exists() and codex_session_notes.is_file():
                notes, _ = read_text(codex_session_notes, args.skip_files_over_bytes)
                write_zip_text(zf, "context/codex-session-notes.md", notes)
            write_zip_text(
                zf,
                "context/bridge-thread-context.md",
                compact_thread_context(
                    thread_file=thread_file,
                    root=root,
                    max_events=args.max_thread_events,
                    max_chars=args.max_thread_chars,
                ),
            )
            if git:
                write_zip_text(zf, "context/git-status.txt", status or "<clean or unavailable>\n")
                write_zip_text(zf, "context/git-diff-stat.txt", diff_stat or "<no diff stat or unavailable>\n")
            for notes_path in extra_notes_paths:
                notes, _ = read_text(notes_path, args.skip_files_over_bytes)
                write_zip_text(zf, f"context/extra-notes/{notes_path.name}", notes)
    else:
        out.write_text(content, encoding="utf-8")
    append_thread_event(
        bridge_dir=bridge_dir,
        bridge_thread_id=bridge_thread_id,
        title=one_line(args.goal, 80),
        now=dt.datetime.now().isoformat(timespec="seconds"),
        event_type="bundle",
        details=[
            f"- Codex session: `{codex_session_id}`",
            f"- Bundle: `{display_path(out, root)}`",
            f"- Bundle format: `{args.format}`",
            f"- Mode: `{args.mode}`",
            f"- Repository context: `{args.repo_context}`",
            f"- Included files: {len(selected)}",
            f"- Omitted files: {len(omitted)}",
            f"- Question: {one_line(args.question)}",
        ],
        codex_session_id=codex_session_id,
    )
    print(str(out))
    if args.format == "zip":
        print(f"Included {len(selected)} files. Zip bytes: {out.stat().st_size}", file=sys.stderr)
    else:
        print(f"Included {len(selected)} files. Approx chars: {len(content)}", file=sys.stderr)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
