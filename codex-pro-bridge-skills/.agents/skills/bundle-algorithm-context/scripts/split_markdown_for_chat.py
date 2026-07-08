#!/usr/bin/env python3
"""Split a markdown bundle into paste/upload-sized parts."""

from __future__ import annotations

import argparse
from pathlib import Path


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("input", help="Markdown bundle to split")
    p.add_argument("--max-chars", type=int, default=60000)
    p.add_argument("--out-dir", default="")
    args = p.parse_args()

    src = Path(args.input).resolve()
    text = src.read_text(encoding="utf-8")
    out_dir = Path(args.out_dir).resolve() if args.out_dir else src.parent / f"{src.stem}-parts"
    out_dir.mkdir(parents=True, exist_ok=True)

    chunks = []
    start = 0
    while start < len(text):
        end = min(start + args.max_chars, len(text))
        if end < len(text):
            # Prefer a section boundary or line break.
            boundary = max(text.rfind("\n## ", start, end), text.rfind("\n### ", start, end), text.rfind("\n", start, end))
            if boundary > start + args.max_chars // 2:
                end = boundary
        chunks.append(text[start:end].strip() + "\n")
        start = end

    total = len(chunks)
    for i, chunk in enumerate(chunks, start=1):
        header = f"# Bundle Part {i}/{total}\n\nThis is part {i} of {total} from `{src.name}`. Wait for all parts before reviewing if pasted sequentially.\n\n"
        path = out_dir / f"{src.stem}.part-{i:03d}-of-{total:03d}.md"
        path.write_text(header + chunk, encoding="utf-8")
        print(path)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
