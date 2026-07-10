#!/usr/bin/env python3
"""Dependency-free structural validation for this skills package."""

from __future__ import annotations

import ast
import re
import subprocess
import sys
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]
SKILLS = PACKAGE / ".agents" / "skills"
ERRORS: list[str] = []


def error(message: str) -> None:
    ERRORS.append(message)


def frontmatter(text: str, path: Path) -> dict[str, str]:
    if not text.startswith("---\n") or "\n---\n" not in text[4:]:
        error(f"{path}: missing YAML frontmatter")
        return {}
    raw = text[4:].split("\n---\n", 1)[0]
    values: dict[str, str] = {}
    for line in raw.splitlines():
        if ":" not in line:
            error(f"{path}: invalid frontmatter line: {line}")
            continue
        key, value = line.split(":", 1)
        values[key.strip()] = value.strip().strip("'\"")
    unknown = set(values) - {"name", "description"}
    if unknown:
        error(f"{path}: unsupported frontmatter keys: {sorted(unknown)}")
    return values


def validate_skill(skill_dir: Path) -> None:
    skill_file = skill_dir / "SKILL.md"
    if not skill_file.is_file():
        error(f"{skill_dir}: missing SKILL.md")
        return
    text = skill_file.read_text(encoding="utf-8")
    values = frontmatter(text, skill_file)
    if values.get("name") != skill_dir.name:
        error(f"{skill_file}: name must match directory")
    if not values.get("description"):
        error(f"{skill_file}: description is required")
    if len(text.splitlines()) > 500:
        error(f"{skill_file}: exceeds 500 lines")

    for reference in sorted((skill_dir / "references").glob("*.md")):
        if reference.name not in text:
            error(f"{reference}: orphan reference; SKILL.md has no context pointer")
    for link in re.findall(r"\[[^]]+\]\(([^)]+\.md)\)", text):
        if "://" in link:
            continue
        target = (skill_dir / link).resolve()
        if not target.is_file():
            error(f"{skill_file}: broken reference {link}")
    for script in sorted((skill_dir / "scripts").glob("*")):
        if script.is_file() and script.name not in text and script.name not in (
            "__init__.py",
        ):
            error(f"{script}: script is not named in SKILL.md")


def main() -> int:
    skill_dirs = sorted(
        path for path in SKILLS.iterdir() if path.is_dir() and not path.name.startswith(".")
    )
    for skill_dir in skill_dirs:
        validate_skill(skill_dir)
    for script in sorted(SKILLS.rglob("*.py")):
        try:
            ast.parse(script.read_text(encoding="utf-8"), filename=str(script))
        except SyntaxError as exc:
            error(f"{script}: {exc}")
    shell_files = [PACKAGE / "install.sh", PACKAGE / "install_global.sh", PACKAGE / "install_repo_local.sh"]
    result = subprocess.run(["bash", "-n", *map(str, shell_files)], text=True, capture_output=True)
    if result.returncode:
        error(result.stderr.strip())
    if ERRORS:
        print("\n".join(f"ERROR: {item}" for item in ERRORS), file=sys.stderr)
        return 1
    print(f"Validated {len(skill_dirs)} skills, Python syntax, references, scripts, and installers.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
