#!/usr/bin/env python3
"""按明确请求组合既有bundle和staging；不创建会话、不上传、不Send。"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import uuid
import zipfile
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
BUILDER_DIR = Path(__file__).resolve().parents[2] / "bundle-algorithm-context/scripts"
sys.path.insert(0, str(SHARED_DIR))
sys.path.insert(0, str(BUILDER_DIR))
from bridge_store import file_sha256
from build_algorithm_bundle import (
    CODEX_NOTES_ARCHIVE_PATH,
    README_ARCHIVE_PATH,
    archive_name,
)
from executor_handoff import CONTEXT_POLICIES


def material_references(request: dict, root: Path) -> list[dict]:
    sources = [(Path(request["notes"]), CODEX_NOTES_ARCHIVE_PATH, "notes")]
    sources.extend((Path(value), archive_name(Path(value), root), "evidence")
                   for value in request["files"])
    return [{"source_path": path.relative_to(root).as_posix(),
             "archive_path": target, "role": role} for path, target, role in sources]


def question_with_material_paths(question: str, materials: list[dict], root: Path) -> str:
    """只替换明确的文件引用；同名文件需要使用相对路径以避免指向错误材料。"""
    aliases: dict[str, set[str]] = {}
    preferred = {}
    for item in materials:
        preferred.setdefault(item["source_path"], item["archive_path"])
    for source, target in preferred.items():
        for alias in (source, str(root / source), Path(source).name):
            aliases.setdefault(alias, set()).add(target)
    # 已经使用包内路径的引用保持原样。
    for item in materials:
        aliases[item["archive_path"]] = {item["archive_path"]}
    aliases[README_ARCHIVE_PATH] = {README_ARCHIVE_PATH}
    pattern = re.compile(r"(?<![A-Za-z0-9_./-])(?:" +
                         "|".join(re.escape(key) for key in sorted(aliases, key=len, reverse=True)) +
                         r")(?![A-Za-z0-9_./-])")
    def replace(match):
        targets = aliases[match.group()]
        if len(targets) != 1:
            raise ValueError(f"ambiguous material reference {match.group()!r}; use its repository-relative path")
        return next(iter(targets))
    return pattern.sub(replace, question)


def material_guide(materials: list[dict], context_policy: str) -> str:
    if context_policy == "none":
        return (
            "## 证据范围\n"
            "本轮不附加仓库文件；仅使用问题和对话上下文。"
            "不要假定存在或要求读取 ZIP 附件。"
        )
    lines = ["## 附件阅读路径", f"先阅读 ZIP 内 `{README_ARCHIVE_PATH}`。材料路径如下："]
    for item in materials:
        lines.append(f"- {json.dumps(item['source_path'], ensure_ascii=False)} → "
                     f"{json.dumps(item['archive_path'], ensure_ascii=False)}")
    lines.append("以上均为附件内路径；不需要访问本地仓库，也不要将原文件名当作另一个缺失附件。")
    return "\n".join(lines)


def read_request(path: Path) -> dict:
    request = json.loads(path.read_text(encoding="utf-8"))
    required = {"repo", "bridge_thread_id", "goal", "question", "notes", "files"}
    optional = {"bridge_project_id", "mode", "file_digests", "context_policy", "max_files"}
    if not isinstance(request, dict) or set(request) - required - optional or required - set(request):
        raise ValueError(
            "request requires repo/bridge_thread_id/goal/question/notes/files; "
            "optional bridge_project_id/mode/file_digests/context_policy/max_files"
        )
    string_keys = (required - {"files"}) | (
        (set(request) & optional) - {"file_digests", "max_files"}
    )
    for key in string_keys:
        if not isinstance(request[key], str) or not request[key].strip():
            raise ValueError(f"{key} must be a nonempty string")
    root = Path(request["repo"]).expanduser()
    if not root.is_absolute() or not root.is_dir():
        raise ValueError("repo must be an absolute existing directory")
    root = root.resolve()
    if not isinstance(request["files"], list):
        raise TypeError("files must be a list")
    context_policy = request.get("context_policy", "explicit")
    if not isinstance(context_policy, str) or context_policy not in CONTEXT_POLICIES:
        raise ValueError("context_policy must be one of: " + ", ".join(CONTEXT_POLICIES))
    if context_policy == "none" and request["files"]:
        raise ValueError("context_policy none cannot be combined with files")
    if context_policy == "explicit" and not request["files"]:
        raise ValueError("context_policy explicit requires at least one file")
    configured_max = request.get("max_files", 24 if context_policy == "auto" else len(request["files"]))
    if isinstance(configured_max, bool) or not isinstance(configured_max, int):
        raise TypeError("max_files must be an integer")
    if configured_max < 0:
        raise ValueError("max_files must not be negative")
    if context_policy == "none" and configured_max != 0:
        raise ValueError("context_policy none requires max_files=0")
    if context_policy == "explicit" and configured_max != len(request["files"]):
        raise ValueError("context_policy explicit requires max_files to equal file count")
    if context_policy == "auto" and configured_max <= 0:
        raise ValueError("max_files must be positive for auto context policy")
    request["context_policy"] = context_policy
    request["max_files"] = configured_max
    for value in [request["notes"], *request["files"]]:
        if not isinstance(value, str) or not value:
            raise ValueError("notes/files must contain nonempty paths")
        file = (root / value).resolve(strict=True)
        if not file.is_file() or not file.is_relative_to(root):
            raise ValueError(f"file must stay inside repo: {value}")
    declared_paths = {
        str((root / value).resolve().relative_to(root).as_posix())
        for value in [request["notes"], *request["files"]]
    }
    if "file_digests" in request:
        digests = request["file_digests"]
        if not isinstance(digests, dict) or set(digests) != declared_paths:
            raise ValueError("file_digests must cover exactly notes and files")
        for relative, expected in digests.items():
            if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
                raise ValueError(f"file_digests[{relative!r}] must be lowercase SHA-256")
            actual = file_sha256(root / relative)
            if actual != expected:
                raise ValueError(f"file drift detected: {relative}")
    request["repo"] = str(root)
    request["notes"] = str((root / request["notes"]).resolve())
    request["files"] = [str((root / value).resolve()) for value in request["files"]]
    return request


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--request", type=Path, required=True, help="明确目标、问题、notes和文件清单的JSON")
    parser.add_argument("--stage", action="store_true", help="同时调用既有Windows暂存器；不上传")
    parser.add_argument("--out", type=Path, help="可选非覆盖bundle路径，须在repo内")
    args = parser.parse_args()
    result = {"ready": False, "phase": "request", "sent": False}
    try:
        request = read_request(args.request)
        root = Path(request["repo"])
        context_policy = request["context_policy"]
        if args.stage and context_policy == "none":
            raise ValueError("context_policy none has no browser attachment to stage")
        if args.out is not None and context_policy == "none":
            raise ValueError("context_policy none cannot create a bundle with --out")
        if context_policy == "none":
            prompt_root = root / ".codex" / "codex-pro-bridge" / "prompts"
            prompt_root.mkdir(parents=True, exist_ok=True)
            prompt_path = prompt_root / f"prepare-{uuid.uuid4().hex}.prompt.md"
            final_prompt = (
                material_guide([], context_policy)
                + "\n\n## 问题\n"
                + request["question"]
                + "\n"
            )
            with prompt_path.open("x", encoding="utf-8") as handle:
                handle.write(final_prompt)
            result.update(
                ready=True,
                phase="prepared",
                return_code=0,
                context_policy=context_policy,
                max_files=0,
                attachment_policy="none",
                prompt_file=str(prompt_path),
                prompt_sha256=hashlib.sha256(final_prompt.encode()).hexdigest(),
                source_files=0,
            )
            print(json.dumps(result, ensure_ascii=False))
            return 0
        bundle = (args.out if args.out is not None else
                  Path(".codex/codex-pro-bridge/bundles") / f"prepare-{uuid.uuid4().hex}.zip")
        bundle = (root / bundle).resolve()
        if not bundle.is_relative_to(root) or bundle.suffix != ".zip" or bundle.exists():
            raise ValueError("out must be a new .zip path inside repo")
        prompt_path = bundle.with_suffix(".prompt.md")
        material_path = bundle.with_suffix(".materials.json")
        if prompt_path.exists() or material_path.exists():
            raise ValueError("prompt/material output already exists; choose a new bundle path")
        materials = material_references(request, root)
        question = (
            request["question"]
            if context_policy == "none"
            else question_with_material_paths(request["question"], materials, root)
        )
        final_prompt = (
            material_guide(materials, context_policy) + "\n\n## 问题\n" + question + "\n"
        )
        skills = Path(__file__).resolve().parents[2]
        command = [sys.executable, str(skills / "bundle-algorithm-context/scripts/build_algorithm_bundle.py"),
                   "--repo", str(root), "--bridge-thread-id", request["bridge_thread_id"],
                   "--goal=" + request["goal"], "--question=" + question,
                   "--codex-session-notes", request["notes"],
                   "--mode", request.get("mode", "implementation_check"),
                   "--repo-context", context_policy, "--git-context", "none", "--format", "zip",
                   "--max-files", str(request["max_files"]), "--out", str(bundle)]
        if "bridge_project_id" in request:
            command += ["--bridge-project-id", request["bridge_project_id"]]
        if request["files"]:
            command += ["--include", *request["files"]]
        result["phase"] = "build"
        completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
        if completed.returncode:
            result.update(return_code=completed.returncode, error=completed.stderr, output=completed.stdout)
            print(json.dumps(result, ensure_ascii=False))
            return completed.returncode
        result.update(
            bundle=str(bundle),
            phase="verify",
            context_policy=context_policy,
            max_files=request["max_files"],
            attachment_policy="none" if context_policy == "none" else "bundle",
        )
        with zipfile.ZipFile(bundle) as archive:
            bad = archive.testzip()
            if bad:
                raise ValueError(f"ZIP integrity failed: {bad}")
            if len(archive.namelist()) != len(set(archive.namelist())):
                raise ValueError("ZIP contains duplicate member paths")
            for item in materials:
                packed = archive.read(item["archive_path"])
                if packed != (root / item["source_path"]).read_bytes():
                    raise ValueError(f"packed material differs from source: {item['source_path']}")
                item["sha256"] = hashlib.sha256(packed).hexdigest()
            result["source_files"] = sum(name.startswith("source/") for name in archive.namelist())
        result["bundle_sha256"] = hashlib.sha256(bundle.read_bytes()).hexdigest()
        result["phase"] = "material-guide"
        with prompt_path.open("x", encoding="utf-8") as handle:
            handle.write(final_prompt)
        with material_path.open("x", encoding="utf-8") as handle:
            json.dump({"bundle_sha256": result["bundle_sha256"], "materials": materials},
                      handle, ensure_ascii=False, indent=2)
            handle.write("\n")
        result.update(prompt_file=str(prompt_path), prompt_sha256=hashlib.sha256(final_prompt.encode()).hexdigest(),
                      materials_file=str(material_path), material_map=materials)
        if args.stage:
            result["phase"] = "stage"
            command = [sys.executable, str(Path(__file__).with_name("manage_browser_staging.py")),
                       "stage", "--source", str(bundle), "--bridge-thread-id", request["bridge_thread_id"]]
            completed = subprocess.run(command, cwd=root, capture_output=True, text=True, check=False)
            if completed.returncode:
                result.update(return_code=completed.returncode, error=completed.stderr, output=completed.stdout)
                print(json.dumps(result, ensure_ascii=False))
                return completed.returncode
            staged = json.loads(completed.stdout)
            if staged.get("source_sha256") != result["bundle_sha256"] or staged.get("ready") is not True:
                raise ValueError("staging result does not match verified bundle")
            result["staging"] = staged
        result.update(ready=True, phase="prepared", return_code=0)
        print(json.dumps(result, ensure_ascii=False))
        return 0
    except (TypeError, ValueError, OSError, KeyError, zipfile.BadZipFile) as exc:
        result.update(error=str(exc), return_code=2)
        print(json.dumps(result, ensure_ascii=False))
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
