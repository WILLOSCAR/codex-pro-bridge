#!/usr/bin/env python3
"""Persist ChatGPT's Copy reply Markdown from the Windows clipboard."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import tempfile


class CaptureError(RuntimeError):
    pass


def markdown_metrics(text: str) -> dict[str, int | str]:
    lines = text.splitlines()
    table_count = 0
    for line in lines:
        if "|" not in line:
            continue
        cells = line.strip().strip("|").split("|")
        if len(cells) >= 2 and all(
            re.fullmatch(r"\s*:?-{3,}:?\s*", cell) for cell in cells
        ):
            table_count += 1
    fence_lines = sum(bool(re.match(r"^\s*```", line)) for line in lines)
    display_delimiters = sum(line.strip() == "$$" for line in lines)
    return {
        "sha256": hashlib.sha256(text.encode("utf-8")).hexdigest(),
        "characters": len(text),
        "lines": len(lines),
        "headings": sum(bool(re.match(r"^#{1,6}\s+", line)) for line in lines),
        "display_math": display_delimiters // 2,
        "inline_math": len(re.findall(r"\\\(", text)),
        "tables": table_count,
        "code_blocks": fence_lines // 2,
        "links": len(re.findall(r"(?<!!)\[[^\]]+\]\([^\n)]+\)", text)),
    }


def read_windows_clipboard() -> str:
    executable = shutil.which("powershell.exe")
    if not executable:
        raise CaptureError("powershell.exe is unavailable; the Windows clipboard cannot be read")
    command = (
        "[Console]::OutputEncoding=[Text.UTF8Encoding]::new($false); "
        "$value=Get-Clipboard -Raw; "
        "if ($null -eq $value) { exit 3 }; "
        "[Console]::Out.Write($value)"
    )
    completed = subprocess.run(
        [executable, "-NoProfile", "-NonInteractive", "-Command", command],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    if completed.returncode != 0:
        detail = completed.stderr.decode("utf-8", errors="replace").strip()
        raise CaptureError(
            f"Windows clipboard read failed with exit {completed.returncode}"
            + (f": {detail}" if detail else "")
        )
    text = completed.stdout.decode("utf-8-sig").replace("\r\n", "\n")
    if not text.strip():
        raise CaptureError("Windows clipboard is empty")
    return text.rstrip() + "\n"


def atomic_write(path: Path, text: str, *, replace: bool) -> None:
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not replace:
        raise CaptureError(f"Refusing to overwrite existing capture: {path}")
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
            handle.write(text)
        os.replace(temporary, path)
    except Exception:
        try:
            os.unlink(temporary)
        except FileNotFoundError:
            pass
        raise


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Save Markdown produced by ChatGPT's visible Copy reply control."
    )
    parser.add_argument("--output", required=True)
    parser.add_argument("--replace", action="store_true")
    for name in (
        "headings",
        "display-math",
        "inline-math",
        "tables",
        "code-blocks",
        "links",
    ):
        parser.add_argument(f"--expected-{name}", type=int)
    args = parser.parse_args()

    try:
        text = read_windows_clipboard()
        metrics = markdown_metrics(text)
        expected = {
            "headings": args.expected_headings,
            "display_math": args.expected_display_math,
            "inline_math": args.expected_inline_math,
            "tables": args.expected_tables,
            "code_blocks": args.expected_code_blocks,
            "links": args.expected_links,
        }
        mismatches = {
            name: {"expected": value, "observed": metrics[name]}
            for name, value in expected.items()
            if value is not None and metrics[name] != value
        }
        if mismatches:
            raise CaptureError(
                "Copied Markdown does not match the target reply structure: "
                + json.dumps(mismatches, ensure_ascii=False, sort_keys=True)
            )
        atomic_write(Path(args.output), text, replace=args.replace)
        print(
            json.dumps(
                {"path": str(Path(args.output).expanduser().resolve()), **metrics},
                ensure_ascii=False,
                sort_keys=True,
            )
        )
        return 0
    except (CaptureError, OSError, UnicodeError) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
