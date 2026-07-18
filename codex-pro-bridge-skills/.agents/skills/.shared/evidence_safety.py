#!/usr/bin/env python3
"""Shared path and secret checks for task bundles and Project Sources."""

from __future__ import annotations

import fnmatch
import re
from pathlib import Path
from typing import Iterable, List

from bridge_store import is_within


DEFAULT_EXCLUDE_DIRS = {
    ".git",
    ".hg",
    ".svn",
    ".idea",
    ".vscode",
    "node_modules",
    "dist",
    "build",
    "target",
    "out",
    "coverage",
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".venv",
    "venv",
    "env",
    ".env",
    "site-packages",
    ".next",
    ".turbo",
    ".codex",
    "wandb",
    "mlruns",
    "checkpoints",
    "outputs",
    "runs",
    "artifacts",
    "data",
    "datasets",
}
SECRET_NAME_PATTERNS = [
    "*.pem",
    "*.key",
    "*.p12",
    "*.pfx",
    "id_rsa*",
    "id_ed25519*",
    ".env*",
    "*.secret*",
    "*credential*",
    "*credentials*",
    "*cookie*",
    "*cookies*",
    "*.sqlite",
    "*.db",
    ".npmrc",
    ".pypirc",
    "netrc",
    ".netrc",
]
HIGH_CONFIDENCE_SECRET_PATTERNS = [
    re.compile(rb"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bAKIA[0-9A-Z]{16}\b"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b"),
    re.compile(rb"\bsk-[A-Za-z0-9_-]{32,}\b"),
]
SECRET_SCAN_CHUNK_BYTES = 64 * 1024
SECRET_SCAN_OVERLAP_BYTES = 256


def display_path(path: Path, root: Path) -> str:
    if is_within(path, root):
        return path.resolve().relative_to(root.resolve()).as_posix()
    return f"external:{path.name}"


def is_excluded_by_name(
    path: Path,
    root: Path,
) -> bool:
    visible = display_path(path, root)
    parts = Path(visible.replace("external:", "")).parts
    if any(part in DEFAULT_EXCLUDE_DIRS for part in parts):
        return True
    base = path.name
    return any(
        fnmatch.fnmatch(base, pattern) or fnmatch.fnmatch(visible, pattern)
        for pattern in SECRET_NAME_PATTERNS
    )


def scan_for_secrets(paths: Iterable[Path], max_bytes: int) -> List[str]:
    flagged: List[str] = []
    for path in paths:
        try:
            remaining = max(0, max_bytes)
            tail = b""
            with path.open("rb") as handle:
                while remaining:
                    chunk = handle.read(min(SECRET_SCAN_CHUNK_BYTES, remaining))
                    if not chunk:
                        break
                    data = tail + chunk
                    if any(
                        pattern.search(data)
                        for pattern in HIGH_CONFIDENCE_SECRET_PATTERNS
                    ):
                        flagged.append(path.name)
                        break
                    tail = data[-SECRET_SCAN_OVERLAP_BYTES:]
                    remaining -= len(chunk)
        except OSError:
            continue
    return flagged
