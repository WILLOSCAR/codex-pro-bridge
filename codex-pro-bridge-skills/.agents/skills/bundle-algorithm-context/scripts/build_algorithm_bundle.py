#!/usr/bin/env python3
"""Build a scoped, immutable evidence bundle for GPT Pro."""

from __future__ import annotations

import argparse
import fnmatch
import glob
import hashlib
import os
import re
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path
from typing import Iterable, List, Sequence, Tuple


SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import (  # noqa: E402
    BridgeError,
    bridge_root,
    compact_thread_context,
    default_codex_session_id,
    file_sha256,
    is_within,
    now_iso,
    parse_metadata,
    repo_relative,
    require_new_output,
    timestamp_slug,
    validate_id,
)
from evidence_graph import dependency_closure  # noqa: E402


DEFAULT_INCLUDE_EXTS = {
    ".py", ".ipynb", ".md", ".txt", ".rst", ".yaml", ".yml", ".json", ".jsonl",
    ".toml", ".ini", ".cfg", ".sh", ".bash", ".zsh", ".sql", ".ts", ".tsx",
    ".js", ".jsx", ".mjs", ".cjs", ".mts", ".cts", ".html", ".css", ".scss",
    ".less", ".go", ".rs", ".java",
    ".scala", ".kt", ".cpp", ".cc", ".c", ".h", ".hpp", ".cu", ".m", ".mm",
    ".swift", ".r", ".jl", ".log",
}
STATIC_OR_BINARY_EXTS = {
    ".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".pdf", ".zip", ".tar",
    ".gz", ".bz2", ".xz", ".7z", ".mp4", ".mov", ".avi", ".mp3", ".wav",
    ".parquet", ".feather", ".arrow", ".pt", ".pth", ".ckpt", ".onnx", ".bin",
}
DEFAULT_EXCLUDE_DIRS = {
    ".git", ".hg", ".svn", ".idea", ".vscode", "node_modules", "dist", "build",
    "target", "out", "coverage", "__pycache__", ".pytest_cache", ".mypy_cache",
    ".ruff_cache", ".tox", ".venv", "venv", "env", ".env", "site-packages", ".next",
    ".turbo", ".codex", "wandb", "mlruns", "checkpoints", "outputs", "runs", "artifacts",
    "data", "datasets",
}
SECRET_NAME_PATTERNS = [
    "*.pem", "*.key", "*.p12", "*.pfx", "id_rsa*", "id_ed25519*", ".env*", "*.secret*",
    "*credential*", "*credentials*", "*cookie*", "*cookies*", "*.sqlite", "*.db", ".npmrc",
    ".pypirc", "netrc", ".netrc",
]
HIGH_CONFIDENCE_SECRET_PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{32,}\b"),
]
ALGORITHM_HINTS = [
    "train", "trainer", "loss", "reward", "rm", "urm", "dpo", "ppo", "grpo", "opd",
    "eval", "evaluate", "metric", "judge", "dataset", "data", "label", "sampler", "sample",
    "rollout", "agent", "tool", "retrieval", "rank", "ranking", "pipeline", "config",
    "experiment", "ablation", "baseline", "model", "inference", "prompt", "rubric",
    "preprocess", "postprocess", "frontend", "backend", "api", "contract",
]


MODE_OUTPUTS = {
    "algorithm_review": """Please return:
1. Problem Restatement
2. Core Hypothesis and Method Decomposition
3. Strongest Arguments For and Against
4. Hidden Assumptions and Baseline Gaps
5. Data, Label, Leakage, Reward, Loss, and Evaluation Risks
6. Ablation Matrix
7. Implementation Checkpoints
8. Minimal Experiment Plan
9. Go / No-Go Judgment""",
    "experiment_analysis": """Please return:
1. Result Restatement
2. What the Evidence Supports
3. Confounders and Validity Risks
4. Metric and Slice Diagnostics
5. Alternative Explanations
6. Minimal Follow-up Experiments
7. Trust / Iterate / Stop Judgment""",
    "paper_brainstorm": """Please return:
1. One-Sentence Claim
2. Novelty Diagnosis
3. Related-Work Pressure, with unverified items marked
4. Strongest Framing Options
5. Required Experiments and Killer Baselines
6. Reviewer Objections
7. Publishability Judgment
8. Next Actions""",
    "implementation_check": """Please return:
1. Checked Evidence
2. Proposal-to-Code Matches
3. Concrete Mismatches
4. Config, Command, Data-Split, Eval, and Metric Risks
5. Result Trustworthiness
6. Required Fixes
7. Re-run Plan""",
    "general_question": """Please return:
1. Direct Answer
2. Key Reasoning and Assumptions
3. Unknowns
4. Risks or Caveats
5. Concrete Next Actions for Codex""",
}


def run_text(command: Sequence[str], cwd: Path) -> str:
    try:
        result = subprocess.run(
            list(command), cwd=str(cwd), text=True, capture_output=True, check=True
        )
        # Preserve the leading status column used by `git status --short`.
        return result.stdout.rstrip()
    except (OSError, subprocess.CalledProcessError):
        return ""


def is_git_repo(root: Path) -> bool:
    return run_text(["git", "rev-parse", "--is-inside-work-tree"], root) == "true"


def git_files(root: Path) -> List[Path]:
    try:
        result = subprocess.run(
            ["git", "ls-files", "-z", "--cached", "--others", "--exclude-standard"],
            cwd=str(root),
            capture_output=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return []
    return [root / os.fsdecode(item) for item in result.stdout.split(b"\0") if item]


def git_changed_paths(root: Path) -> set[str]:
    """Return staged, unstaged, and untracked paths without porcelain quoting loss."""
    commands = []
    if run_text(["git", "rev-parse", "--verify", "HEAD"], root):
        commands.append(["git", "diff", "--name-only", "-z", "HEAD", "--"])
    else:
        commands.append(["git", "ls-files", "-z", "--cached"])
    commands.append(["git", "ls-files", "-z", "--others", "--exclude-standard"])
    changed: set[str] = set()
    for command in commands:
        try:
            result = subprocess.run(command, cwd=str(root), capture_output=True, check=True)
        except (OSError, subprocess.CalledProcessError):
            continue
        changed.update(os.fsdecode(item) for item in result.stdout.split(b"\0") if item)
    return changed


def walk_files(root: Path) -> List[Path]:
    files: List[Path] = []
    for dirpath, dirnames, filenames in os.walk(root):
        dirnames[:] = [name for name in dirnames if name not in DEFAULT_EXCLUDE_DIRS]
        files.extend(Path(dirpath) / name for name in filenames)
    return files


def display_path(path: Path, root: Path) -> str:
    if is_within(path, root):
        return path.resolve().relative_to(root.resolve()).as_posix()
    return f"external:{path.name}"


def is_excluded_by_name(path: Path, root: Path) -> bool:
    visible = display_path(path, root)
    parts = Path(visible.replace("external:", "")).parts
    if any(part in DEFAULT_EXCLUDE_DIRS for part in parts):
        return True
    base = path.name
    return any(fnmatch.fnmatch(base, pattern) or fnmatch.fnmatch(visible, pattern) for pattern in SECRET_NAME_PATTERNS)


def is_candidate(path: Path, root: Path, include_logs: bool) -> bool:
    if is_excluded_by_name(path, root):
        return False
    extension = path.suffix.lower()
    if extension in STATIC_OR_BINARY_EXTS:
        return False
    if extension == ".log" and not include_logs:
        return False
    return extension in DEFAULT_INCLUDE_EXTS or path.name in {
        "README", "AGENTS", "Makefile", "Dockerfile"
    }


def file_size(path: Path) -> int:
    try:
        return path.stat().st_size
    except OSError:
        return 0


def read_text(path: Path, max_bytes: int) -> Tuple[str, bool]:
    data = path.read_bytes()
    truncated = len(data) > max_bytes
    return data[:max_bytes].decode("utf-8", errors="replace"), truncated


def scan_for_secrets(paths: Iterable[Path], max_bytes: int) -> List[str]:
    flagged: List[str] = []
    for path in paths:
        try:
            data = path.read_bytes()[:max_bytes]
        except OSError:
            continue
        if any(pattern.search(data) for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS):
            flagged.append(path.name)
    return flagged


def keywords_from_text(*texts: str) -> List[str]:
    words = re.findall(r"[A-Za-z_][A-Za-z0-9_-]{2,}", "\n".join(texts).lower())
    stop = {
        "the", "and", "for", "with", "this", "that", "from", "into", "what", "when",
        "where", "which", "about", "should", "could", "would", "please", "need",
    }
    return list(dict.fromkeys(word for word in words if word not in stop))[:80]


def score_file(path: Path, root: Path, keywords: Iterable[str], changed: set[str]) -> int:
    relative = display_path(path, root).lower()
    name = path.name.lower()
    score = 12 if display_path(path, root) in changed else 0
    if name in {
        "readme.md", "agents.md", "pyproject.toml", "package.json", "requirements.txt",
        "environment.yml",
    }:
        score += 8
    score += sum(3 for hint in ALGORITHM_HINTS if hint in relative)
    # A path named by the goal/question outranks generic changed-file breadth.
    score += sum(16 for keyword in keywords if keyword and keyword in relative)
    if path.suffix.lower() in {".yaml", ".yml", ".toml", ".json", ".ini", ".cfg"}:
        score += 2
    if path.suffix.lower() in {".md", ".rst", ".txt"}:
        score += 2
    return score


def select_auto_evidence(
    *,
    ranked: Sequence[Path],
    required: Sequence[Path],
    candidates: Sequence[Path],
    root: Path,
    max_files: int,
    skip_files_over_bytes: int,
    allow_incomplete: bool,
) -> Tuple[List[Path], dict[Path, str], List[Tuple[str, str]]]:
    """Select relevance seeds while keeping every admitted dependency closure whole."""
    eligible = [
        path.resolve()
        for path in candidates
        if skip_files_over_bytes <= 0 or file_size(path) <= skip_files_over_bytes
    ]
    eligible_set = set(eligible)
    required_set = {path.resolve() for path in required}
    seed_order = list(
        dict.fromkeys(
            [path.resolve() for path in required]
            + [path.resolve() for path in ranked if path.resolve() in eligible_set]
        )
    )
    selected: List[Path] = []
    selected_set: set[Path] = set()
    reasons: dict[Path, str] = {}
    omitted: List[Tuple[str, str]] = []

    for seed in seed_order:
        if seed in selected_set:
            continue
        closure = dependency_closure([seed], eligible, root)
        if closure.unresolved and not allow_incomplete:
            raise BridgeError(
                "Auto dependency closure is incomplete for "
                f"{display_path(seed, root)}: " + "; ".join(closure.unresolved)
            )
        for item in closure.unresolved:
            omitted.append((item, "unresolved local dependency; explicitly allowed"))
        additions = [path for path in closure.paths if path not in selected_set]
        if len(selected) + len(additions) > max_files:
            message = (
                f"dependency closure needs {len(additions)} more files with "
                f"{max_files - len(selected)} slots remaining"
            )
            if seed in required_set or not selected:
                raise BridgeError(
                    f"Auto dependency closure for {display_path(seed, root)} exceeds --max-files: "
                    + message
                )
            omitted.append((display_path(seed, root), message))
            continue
        for path in additions:
            selected.append(path)
            selected_set.add(path)
            if path == seed:
                reasons[path] = (
                    "explicit auto focus"
                    if seed in required_set
                    else "relevance-ranked focus"
                )
            else:
                reasons[path] = closure.reasons[path]
        if len(selected) == max_files:
            break

    for path in ranked:
        resolved = path.resolve()
        if resolved not in eligible_set:
            omitted.append(
                (display_path(path, root), f"over size threshold ({file_size(path)} bytes)")
            )
    return selected, reasons, omitted


def _expand_item(item: str, root: Path) -> List[Path]:
    has_magic = glob.has_magic(item)
    if has_magic:
        if Path(item).is_absolute():
            matches = [Path(value) for value in glob.glob(item, recursive=True)]
        else:
            matches = list(root.glob(item))
    else:
        candidate = Path(item)
        candidate = candidate if candidate.is_absolute() else root / candidate
        matches = [candidate] if candidate.exists() else []
    expanded: List[Path] = []
    for match in matches:
        resolved = match.resolve()
        if resolved.is_dir():
            expanded.extend(path.resolve() for path in resolved.rglob("*") if path.is_file())
        elif resolved.is_file():
            expanded.append(resolved)
    return expanded


def parse_include_paths(
    items: Sequence[str], root: Path, *, allow_external: bool
) -> Tuple[List[Path], List[str]]:
    paths: List[Path] = []
    problems: List[str] = []
    for item in items:
        item_path = Path(item)
        item_label = (
            f"external:{item_path.name or '<pattern>'}"
            if item_path.is_absolute() or ".." in item_path.parts
            else item
        )
        matches = _expand_item(item, root)
        if not matches:
            problems.append(f"include did not match a file: {item_label}")
            continue
        for path in matches:
            if not is_within(path, root) and not allow_external:
                problems.append(f"include escapes repository root: {item_label}")
                continue
            paths.append(path)
    return list(dict.fromkeys(paths)), list(dict.fromkeys(problems))


def filter_files(
    paths: Iterable[Path],
    root: Path,
    *,
    include_logs: bool,
    max_files: int,
    skip_files_over_bytes: int,
    allow_external: bool = False,
) -> Tuple[List[Path], List[Tuple[str, str]]]:
    selected: List[Path] = []
    omitted: List[Tuple[str, str]] = []
    for path in paths:
        label = display_path(path, root)
        if not is_within(path, root) and not allow_external:
            omitted.append((label, "resolved path escapes repository root"))
        elif not path.is_file():
            omitted.append((label, "not a file"))
        elif not is_candidate(path, root, include_logs):
            omitted.append((label, "excluded by path, name, extension, or binary policy"))
        elif skip_files_over_bytes > 0 and file_size(path) > skip_files_over_bytes:
            omitted.append((label, f"over size threshold ({file_size(path)} bytes)"))
        elif len(selected) >= max_files:
            omitted.append((label, f"max file count reached ({max_files})"))
        else:
            selected.append(path)
    return selected, omitted


def markdown_code_fence(path: Path, content: str) -> str:
    extension = path.suffix.lower().lstrip(".") or "text"
    language = {
        "py": "python", "md": "markdown", "yml": "yaml", "jsonl": "json", "sh": "bash",
        "bash": "bash", "zsh": "bash", "ts": "typescript", "js": "javascript",
    }.get(extension, extension)
    return f"```{language}\n{content.replace('```', '``\u200b`')}\n```"


def archive_name(path: Path, root: Path) -> str:
    if is_within(path, root):
        return f"source/{path.resolve().relative_to(root.resolve()).as_posix()}"
    fingerprint = hashlib.sha256(str(path).encode("utf-8")).hexdigest()[:10]
    return f"source/external/{fingerprint}-{path.name}"


def extra_notes_archive_names(paths: Sequence[Path]) -> List[Tuple[Path, str]]:
    seen: set[str] = set()
    result: List[Tuple[Path, str]] = []
    for path in paths:
        name = path.name
        if name in seen:
            name = f"{hashlib.sha256(str(path).encode()).hexdigest()[:8]}-{name}"
        seen.add(name)
        result.append((path, f"context/extra-notes/{name}"))
    return result


def filtered_git_status(root: Path) -> str:
    lines = run_text(["git", "status", "--short"], root).splitlines()
    return "\n".join(line for line in lines if not line[3:].startswith(".codex/"))


def git_diff_stat(root: Path) -> str:
    if run_text(["git", "rev-parse", "--verify", "HEAD"], root):
        return run_text(["git", "diff", "--stat", "HEAD", "--"], root)
    parts = [
        run_text(["git", "diff", "--stat", "--cached", "--"], root),
        run_text(["git", "diff", "--stat", "--"], root),
    ]
    return "\n".join(part for part in parts if part)


def main() -> int:
    parser = argparse.ArgumentParser(description="Build an immutable GPT Pro context bundle.")
    parser.add_argument("--repo", default=".", help="Repository root.")
    parser.add_argument("--repo-label", default="", help="Safe label shown to GPT Pro; defaults to directory name.")
    parser.add_argument("--bridge-thread-id", required=True, help="Canonical task id.")
    parser.add_argument("--codex-session-id", default="", help="Defaults to <bridge-thread-id>-codex.")
    parser.add_argument("--goal", required=True, help="User goal.")
    parser.add_argument("--question", default="", help="Question for GPT Pro.")
    parser.add_argument("--mode", default="algorithm_review", choices=sorted(MODE_OUTPUTS))
    parser.add_argument("--repo-context", default="auto", choices=["auto", "explicit", "none"])
    parser.add_argument("--include", nargs="*", default=[], help="Explicit repository files, directories, or globs.")
    parser.add_argument("--allow-external-include", action="store_true", help="Allow explicitly requested files outside the repo; archive paths remain anonymized.")
    parser.add_argument(
        "--allow-missing-includes",
        "--allow-incomplete-includes",
        dest="allow_missing_includes",
        action="store_true",
        help="Continue when an explicit include is missing or filtered; the manifest records it.",
    )
    parser.add_argument(
        "--allow-incomplete-auto-context",
        action="store_true",
        help="Continue when a definitely-local dependency cannot be resolved; the manifest records it.",
    )
    parser.add_argument("--allow-secret-like-content", action="store_true", help="Continue after high-confidence secret-pattern detection. Does not rewrite content.")
    parser.add_argument("--max-files", type=int, default=24)
    parser.add_argument("--max-file-bytes", type=int, default=60_000)
    parser.add_argument("--max-total-chars", type=int, default=180_000)
    parser.add_argument("--max-thread-events", type=int, default=24)
    parser.add_argument("--max-thread-chars", type=int, default=20_000)
    parser.add_argument("--skip-files-over-bytes", type=int, default=250_000)
    parser.add_argument("--include-logs", action="store_true")
    parser.add_argument("--codex-session-notes", default="", help="Defaults to the current notes for the derived Codex session.")
    parser.add_argument("--allow-missing-codex-session-notes", action="store_true")
    parser.add_argument("--notes", default="", help="Deprecated alias for --codex-session-notes.")
    parser.add_argument("--extra-notes", nargs="*", default=[])
    parser.add_argument("--format", default="zip", choices=["markdown", "zip"])
    parser.add_argument("--out", default="", help="New output path under the repository. Existing files are never overwritten.")
    args = parser.parse_args()

    try:
        root = Path(args.repo).resolve()
        if not root.is_dir():
            raise BridgeError(f"Repository root is not a directory: {root}")
        if args.max_files < 0 or (args.max_files == 0 and args.repo_context != "none"):
            raise BridgeError(
                "--max-files must be positive unless --repo-context none is used"
            )
        for name in (
            "max_file_bytes", "max_total_chars", "max_thread_events",
            "max_thread_chars", "skip_files_over_bytes",
        ):
            if getattr(args, name) <= 0:
                raise BridgeError(f"--{name.replace('_', '-')} must be positive")
        if args.repo_context == "none" and args.include:
            raise BridgeError("--repo-context none cannot be combined with --include")
        if args.repo_context == "explicit" and not args.include:
            raise BridgeError("--repo-context explicit requires --include")
        if args.allow_external_include and args.repo_context != "explicit":
            raise BridgeError("--allow-external-include requires --repo-context explicit")

        thread_id = validate_id(args.bridge_thread_id, "bridge thread id")
        codex_session_id = validate_id(
            args.codex_session_id or default_codex_session_id(thread_id), "Codex session id"
        )
        bridge_dir = bridge_root(root)
        session_meta = parse_metadata(
            bridge_dir / "codex-sessions" / codex_session_id / "session.md"
        )
        bound_thread = session_meta.get("bridge_thread_id", "")
        if bound_thread and bound_thread != thread_id:
            raise BridgeError(
                f"Codex session {codex_session_id} is bound to {bound_thread}, not {thread_id}"
            )

        notes_arg = args.codex_session_notes or args.notes
        latest_snapshot = session_meta.get("latest_snapshot", "")
        notes_path = (
            (root / notes_arg).resolve()
            if notes_arg and not Path(notes_arg).is_absolute()
            else Path(notes_arg).resolve()
            if notes_arg
            else (root / latest_snapshot).resolve()
            if latest_snapshot
            else bridge_dir / "codex-sessions" / codex_session_id / "notes.md"
        )
        if not is_within(notes_path, root):
            raise BridgeError("Codex session notes must stay under the repository root")
        canonical_notes_root = bridge_dir / "codex-sessions"
        if not is_within(notes_path, canonical_notes_root) and is_excluded_by_name(notes_path, root):
            raise BridgeError("Codex session notes path is excluded by the safety policy")
        if not notes_path.is_file() and not args.allow_missing_codex_session_notes:
            raise BridgeError(
                "Codex session notes are required; run prepare_codex_session_notes.py first"
            )
        if notes_path.is_file() and file_size(notes_path) > args.skip_files_over_bytes:
            raise BridgeError("Codex session notes exceed the configured size threshold")

        include_paths, include_problems = parse_include_paths(
            args.include, root, allow_external=args.allow_external_include
        )
        if include_problems and not args.allow_missing_includes:
            raise BridgeError("; ".join(include_problems))

        git = is_git_repo(root)
        status = filtered_git_status(root) if git else ""
        diff_stat = git_diff_stat(root) if git else ""
        changed = git_changed_paths(root) if git else set()
        omitted: List[Tuple[str, str]] = [(problem, "explicitly allowed") for problem in include_problems]
        selected_reasons: dict[Path, str] = {}
        auto_context_status = "not-applicable"
        if args.repo_context == "none":
            selected: List[Path] = []
        elif args.repo_context == "explicit":
            selected, filtered = filter_files(
                include_paths,
                root,
                include_logs=True,
                max_files=args.max_files,
                skip_files_over_bytes=args.skip_files_over_bytes,
                allow_external=args.allow_external_include,
            )
            if filtered and not args.allow_missing_includes:
                details = "; ".join(f"{label}: {reason}" for label, reason in filtered)
                raise BridgeError("Explicit evidence is incomplete: " + details)
            omitted.extend(filtered)
            selected_reasons = {path.resolve(): "explicit include" for path in selected}
        else:
            all_files = git_files(root) if git else walk_files(root)
            candidates = [
                path for path in all_files
                if path.is_file()
                and is_within(path, root)
                and is_candidate(path, root, args.include_logs)
            ]
            keywords = keywords_from_text(args.goal, args.question)
            ranked = sorted(
                candidates,
                key=lambda path: (
                    score_file(path, root, keywords, changed),
                    -len(display_path(path, root)),
                ),
                reverse=True,
            )
            scored = [
                path for path in ranked if score_file(path, root, keywords, changed) > 0
            ]
            required, filtered = filter_files(
                include_paths,
                root,
                include_logs=True,
                max_files=args.max_files,
                skip_files_over_bytes=args.skip_files_over_bytes,
                allow_external=args.allow_external_include,
            )
            if filtered and not args.allow_missing_includes:
                details = "; ".join(f"{label}: {reason}" for label, reason in filtered)
                raise BridgeError("Explicit auto focus is incomplete: " + details)
            omitted.extend(filtered)
            selected, selected_reasons, auto_omitted = select_auto_evidence(
                ranked=scored,
                required=required,
                candidates=candidates,
                root=root,
                max_files=args.max_files,
                skip_files_over_bytes=args.skip_files_over_bytes,
                allow_incomplete=args.allow_incomplete_auto_context,
            )
            omitted.extend(auto_omitted)
            auto_context_status = (
                "incomplete"
                if any("unresolved local dependency" in reason for _, reason in auto_omitted)
                else "complete"
            )

        extra_notes, extra_problems = parse_include_paths(
            args.extra_notes, root, allow_external=False
        )
        if extra_problems:
            raise BridgeError("; ".join(extra_problems))
        unsafe_extra_notes = [
            display_path(path, root)
            for path in extra_notes
            if (not is_within(path, bridge_dir) and is_excluded_by_name(path, root))
            or file_size(path) > args.skip_files_over_bytes
        ]
        if unsafe_extra_notes:
            raise BridgeError(
                "Extra notes violate the path/name/size safety policy: "
                + ", ".join(unsafe_extra_notes)
            )
        safety_inputs = list(selected) + list(extra_notes)
        if notes_path.is_file():
            safety_inputs.append(notes_path)
        secret_flags = scan_for_secrets(safety_inputs, args.skip_files_over_bytes)
        if secret_flags and not args.allow_secret_like_content:
            raise BridgeError(
                "High-confidence secret-like content found in: "
                + ", ".join(sorted(set(secret_flags)))
                + ". Remove it or pass --allow-secret-like-content after manual review."
            )

        suffix = ".zip" if args.format == "zip" else ".md"
        if args.out:
            out = Path(args.out)
            out = out.resolve() if out.is_absolute() else (root / out).resolve()
        else:
            out = (
                bridge_dir
                / "bundles"
                / f"{timestamp_slug()}-{args.mode}-{uuid.uuid4().hex[:8]}-context{suffix}"
            )
        if not is_within(out, root):
            raise BridgeError("Bundle output must stay under the repository root")
        if out.suffix.lower() != suffix:
            raise BridgeError(f"--out must end in {suffix} for --format {args.format}")
        out.parent.mkdir(parents=True, exist_ok=True)
        require_new_output(out)

        repo_label = args.repo_label.strip() or root.name
        if any(character in repo_label for character in ("/", "\\", "\n", "\r")):
            raise BridgeError("--repo-label must be a short label, not a path")
        if len(repo_label) > 100:
            raise BridgeError("--repo-label must be at most 100 characters")
        thread_context = compact_thread_context(
            root,
            thread_id,
            max_events=args.max_thread_events,
            max_chars=args.max_thread_chars,
        )
        branch = run_text(["git", "branch", "--show-current"], root) if git else ""
        commit = run_text(["git", "rev-parse", "--short", "HEAD"], root) if git else ""
        sections: List[str] = [
            "# GPT Pro Evidence Bundle",
            "",
            "## Metadata",
            f"- Generated: {now_iso()}",
            f"- Repository label: `{repo_label}`",
            f"- Mode: `{args.mode}`",
            f"- Repository context: `{args.repo_context}`",
            f"- Auto dependency closure: `{auto_context_status}`",
            f"- Bridge thread id: `{thread_id}`",
            f"- Codex session id: `{codex_session_id}`",
        ]
        if git:
            sections.extend(
                [f"- Git branch: `{branch or '<unknown>'}`", f"- Git commit: `{commit or '<uncommitted>'}`"]
            )
        sections.extend(
            [
                "",
                "## User Goal",
                args.goal.strip(),
                "",
                "## Question for GPT Pro",
                args.question.strip() or "Answer the scoped question using only the supplied evidence.",
                "",
                "## Evidence Contract",
                "Treat this package as partial evidence. Do not assume access to unlisted files. "
                "Separate observed facts from inference and state missing evidence explicitly.",
                "",
                "### Codex notes",
                f"- `context/codex-session-notes.md`" if notes_path.is_file() else "- Missing by explicit override.",
                "",
                "### Thread context",
                "- `context/bridge-thread-context.md`",
                "",
                "### Repository files",
            ]
        )
        if selected:
            sections.extend(
                f"- `{archive_name(path, root)}` — {selected_reasons.get(path.resolve(), 'selected evidence')}"
                for path in selected
            )
        else:
            sections.append("- No repository files were supplied for this round.")
        if omitted:
            sections.extend(["", "### Omitted or unresolved inputs"])
            sections.extend(f"- `{label}`: {reason}" for label, reason in omitted[:80])
        sections.extend(
            [
                "",
                "Files not listed above were not supplied. Secret/env files, credentials, cookies, "
                "private keys, databases, raw data, vendor trees, and large artifacts are excluded by policy.",
                "",
                "## Git Status",
                markdown_code_fence(Path("git-status.txt"), status or "<clean or unavailable>"),
                "",
                "## Git Diff Stat",
                markdown_code_fence(Path("git-diff-stat.txt"), diff_stat or "<no diff stat or unavailable>"),
                "",
                "## Supplied Source Files",
            ]
        )
        if args.format == "zip":
            sections.extend(
                [f"- `{archive_name(path, root)}`" for path in selected]
                or ["- None for this round."]
            )
        else:
            if not selected:
                sections.append("No repository files were supplied for this round.")
            for path in selected:
                text, truncated = read_text(path, args.max_file_bytes)
                sections.extend(["", f"### `{display_path(path, root)}`"])
                if truncated:
                    sections.append(f"_Truncated to the first {args.max_file_bytes} bytes._")
                sections.append(markdown_code_fence(path, text))
        sections.extend(["", "## Requested Output", MODE_OUTPUTS[args.mode]])
        manifest = "\n".join(sections).strip() + "\n"

        if args.format == "markdown":
            note_text = notes_path.read_text(encoding="utf-8") if notes_path.is_file() else "_Unavailable._"
            full = (
                manifest
                + "\n## Codex Session Notes\n\n"
                + note_text
                + "\n## Compact Bridge Thread Context\n\n"
                + thread_context
            )
            for note in extra_notes:
                full += f"\n## Extra Notes: {note.name}\n\n{note.read_text(encoding='utf-8')}\n"
            if len(full) > args.max_total_chars:
                raise BridgeError(
                    f"Markdown bundle is {len(full)} characters, over --max-total-chars; "
                    "use zip or reduce the selected evidence"
                )
            temp = out.with_name(f".{out.name}.{uuid.uuid4().hex}.tmp")
            try:
                temp.write_text(full, encoding="utf-8")
                os.replace(temp, out)
            finally:
                if temp.exists():
                    temp.unlink()
        else:
            temp = out.with_name(f".{out.name}.{uuid.uuid4().hex}.tmp")
            try:
                with zipfile.ZipFile(temp, "w", compression=zipfile.ZIP_DEFLATED) as archive:
                    archive.writestr("README_FOR_GPT_PRO.md", manifest.encode("utf-8"))
                    for path in selected:
                        archive.write(path, archive_name(path, root))
                    if notes_path.is_file():
                        archive.write(notes_path, "context/codex-session-notes.md")
                    archive.writestr("context/bridge-thread-context.md", thread_context.encode("utf-8"))
                    archive.writestr("context/git-status.txt", (status or "<clean or unavailable>\n").encode())
                    archive.writestr("context/git-diff-stat.txt", (diff_stat or "<no diff stat or unavailable>\n").encode())
                    for note, name in extra_notes_archive_names(extra_notes):
                        archive.write(note, name)
                with zipfile.ZipFile(temp) as archive:
                    bad_member = archive.testzip()
                    if bad_member:
                        raise BridgeError(f"Generated zip failed integrity check at {bad_member}")
                os.replace(temp, out)
            finally:
                if temp.exists():
                    temp.unlink()

        print(out)
        print(
            f"Included {len(selected)} repository files; sha256={file_sha256(out)}. "
            "The task ledger is updated only when this artifact is actually sent.",
            file=sys.stderr,
        )
        return 0
    except (BridgeError, OSError, zipfile.BadZipFile) as exc:
        parser.error(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
