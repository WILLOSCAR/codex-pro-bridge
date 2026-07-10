#!/usr/bin/env python3
"""Resolve a conservative local source dependency closure.

Only definitely-local imports are followed: relative JavaScript/TypeScript
specifiers and relative Python imports. Package imports remain outside the
evidence graph.
"""

from __future__ import annotations

import ast
import re
from collections import deque
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable


JAVASCRIPT_RESOLVE_EXTENSIONS = (
    ".ts",
    ".tsx",
    ".mts",
    ".cts",
    ".js",
    ".jsx",
    ".mjs",
    ".cjs",
    ".json",
    ".css",
    ".scss",
    ".less",
)
JAVASCRIPT_EXTENSIONS = {".ts", ".tsx", ".mts", ".cts", ".js", ".jsx", ".mjs", ".cjs"}
IGNORED_ASSET_EXTENSIONS = {
    ".png",
    ".jpg",
    ".jpeg",
    ".gif",
    ".webp",
    ".svg",
    ".ico",
    ".woff",
    ".woff2",
    ".ttf",
    ".mp3",
    ".wav",
    ".mp4",
    ".mov",
}
JS_IMPORT_RE = re.compile(
    r"(?:\bfrom\s*|\bimport\s*\(\s*|\bimport\s*|\brequire\s*\(\s*)"
    r"[\"']([^\"']+)[\"']"
)


@dataclass(frozen=True)
class DependencyClosure:
    paths: tuple[Path, ...]
    reasons: dict[Path, str]
    unresolved: tuple[str, ...]


def _variants(
    base: Path,
    extensions: tuple[str, ...],
    *,
    index_name: str,
) -> list[Path]:
    variants = [base]
    suffix = base.suffix.lower()
    if suffix:
        variants.extend(base.with_suffix(extension) for extension in extensions)
    else:
        variants.extend(base.with_suffix(extension) for extension in extensions)
        variants.extend(base / f"{index_name}{extension}" for extension in extensions)
    return list(dict.fromkeys(path.resolve() for path in variants))


def _resolve_base(
    base: Path,
    available: set[Path],
    extensions: tuple[str, ...],
    *,
    index_name: str,
) -> Path | None:
    return next(
        (
            candidate
            for candidate in _variants(base, extensions, index_name=index_name)
            if candidate in available
        ),
        None,
    )


def _javascript_dependencies(path: Path, available: set[Path]) -> tuple[list[tuple[Path, str]], list[str]]:
    text = path.read_text(encoding="utf-8", errors="replace")
    dependencies: list[tuple[Path, str]] = []
    unresolved: list[str] = []
    for raw_specifier in JS_IMPORT_RE.findall(text):
        specifier = raw_specifier.split("?", 1)[0].split("#", 1)[0]
        if not specifier.startswith("."):
            continue
        suffix = Path(specifier).suffix.lower()
        if suffix in IGNORED_ASSET_EXTENSIONS:
            continue
        resolved = _resolve_base(
            path.parent / specifier,
            available,
            JAVASCRIPT_RESOLVE_EXTENSIONS,
            index_name="index",
        )
        if resolved is None:
            unresolved.append(specifier)
        else:
            dependencies.append((resolved, specifier))
    return dependencies, unresolved


def _python_dependencies(path: Path, available: set[Path]) -> tuple[list[tuple[Path, str]], list[str]]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8", errors="replace"))
    except SyntaxError:
        return [], []
    dependencies: list[tuple[Path, str]] = []
    unresolved: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.ImportFrom) or node.level <= 0:
            continue
        base_dir = path.parent
        for _ in range(node.level - 1):
            base_dir = base_dir.parent
        modules = [node.module] if node.module else [alias.name for alias in node.names]
        for module in modules:
            specifier = "." * node.level + (module or "")
            module_path = base_dir.joinpath(*(module or "").split("."))
            resolved = _resolve_base(
                module_path, available, (".py",), index_name="__init__"
            )
            if resolved is None:
                unresolved.append(specifier)
            else:
                dependencies.append((resolved, specifier))
    return dependencies, unresolved


def dependency_closure(seeds: Iterable[Path], available_paths: Iterable[Path], root: Path) -> DependencyClosure:
    """Return seeds plus their definitely-local transitive dependencies."""
    root = root.resolve()
    available = {path.resolve() for path in available_paths}
    queue = deque(path.resolve() for path in seeds)
    ordered: list[Path] = []
    reasons: dict[Path, str] = {path.resolve(): "focus" for path in seeds}
    seen: set[Path] = set()
    unresolved: list[str] = []

    while queue:
        path = queue.popleft()
        if path in seen:
            continue
        seen.add(path)
        if path not in available:
            unresolved.append(f"{path.name}: unavailable or excluded")
            continue
        ordered.append(path)
        if path.suffix.lower() in JAVASCRIPT_EXTENSIONS:
            dependencies, missing = _javascript_dependencies(path, available)
        elif path.suffix.lower() == ".py":
            dependencies, missing = _python_dependencies(path, available)
        else:
            dependencies, missing = [], []
        relative = path.relative_to(root).as_posix()
        unresolved.extend(f"{relative} -> {specifier}" for specifier in missing)
        for dependency, _specifier in dependencies:
            if dependency not in seen:
                reasons.setdefault(dependency, f"dependency of {relative}")
                queue.append(dependency)

    # Queue insertion marks dependencies early; rebuild reasons while keeping the
    # first parent explanation and ensuring every ordered path has an entry.
    final_reasons: dict[Path, str] = {}
    for path in ordered:
        final_reasons[path] = reasons[path]
    return DependencyClosure(tuple(ordered), final_reasons, tuple(dict.fromkeys(unresolved)))
