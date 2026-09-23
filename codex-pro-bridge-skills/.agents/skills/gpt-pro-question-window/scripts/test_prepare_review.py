"""真实子进程调用既有builder/staging；独立目录，不调用浏览器或模型。"""
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

SCRIPT = Path(__file__).with_name("prepare_review.py")
BUILDER = SCRIPT.resolve().parents[2] / "bundle-algorithm-context/scripts/build_algorithm_bundle.py"


class PrepareReviewTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name).resolve()
        (self.root / "facts.md").write_text("当前原始说明；不能省略否定条件。\n")
        (self.root / "evidence.md").write_text("完整证据\n")
        self.request = {"repo": str(self.root), "bridge_thread_id": "fixture-review",
                        "goal": "不要省略条件；`echo x` $(echo y)", "question": "哪些结论不成立？",
                        "notes": "facts.md", "files": ["evidence.md"]}

    def tearDown(self):
        self.temp.cleanup()

    def call(self, *args, env=None):
        request_file = self.root / "request.json"
        request_file.write_text(json.dumps(self.request, ensure_ascii=False))
        completed = subprocess.run(
            [sys.executable, str(SCRIPT), "--request", str(request_file), *args],
            capture_output=True,
            text=True,
            env=env,
            check=False,
        )
        return completed, json.loads(completed.stdout)

    def test_full_originals_and_no_git_metadata(self):
        subprocess.run(["git", "init", "-q", str(self.root)], check=True)
        (self.root / "unrelated-visible-only-in-git.txt").write_text("unrelated")
        content = ("正文不能截断，末尾例外仍属于用户要求。\n" * 2000).encode()
        (self.root / "evidence.md").write_bytes(content)
        result, data = self.call()
        self.assertEqual(result.returncode, 0, data)
        self.assertTrue(data["ready"])
        self.assertFalse(data["sent"])
        with zipfile.ZipFile(data["bundle"]) as archive:
            self.assertEqual(archive.read("source/evidence.md"), content)
            self.assertEqual(archive.read("context/codex-session-notes.md"), (self.root / "facts.md").read_bytes())
            self.assertNotIn("context/git-status.txt", archive.namelist())
            overview = archive.read("README_FOR_GPT_PRO.md").decode()
            self.assertIn(self.request["goal"], overview)
            self.assertNotIn("unrelated-visible-only-in-git.txt", overview)

    def test_builder_error_is_not_success(self):
        (self.root / ".env").write_text("fixture only")
        self.request["files"] = [".env"]
        result, data = self.call("--stage")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(data["phase"], "build")
        self.assertFalse(data["ready"])
        self.assertNotIn("staging", data)

    def test_prompt_uses_verified_archive_paths_for_notes_and_evidence(self):
        self.request["question"] = "请先阅读facts.md，再读 `evidence.md`。不要更改科学判断。"
        result, data = self.call()
        self.assertEqual(result.returncode, 0, data)
        prompt = Path(data["prompt_file"]).read_text()
        self.assertIn("请先阅读context/codex-session-notes.md，再读 `source/evidence.md`。不要更改科学判断。", prompt)
        self.assertIn('"facts.md" → "context/codex-session-notes.md"', prompt)
        self.assertEqual(data["prompt_sha256"], hashlib.sha256(prompt.encode()).hexdigest())
        mapping = json.loads(Path(data["materials_file"]).read_text())
        self.assertEqual(mapping["bundle_sha256"], data["bundle_sha256"])
        with zipfile.ZipFile(data["bundle"]) as archive:
            for item in mapping["materials"]:
                self.assertEqual(hashlib.sha256(archive.read(item["archive_path"])).hexdigest(), item["sha256"])
            self.assertIn("请先阅读context/codex-session-notes.md", archive.read("README_FOR_GPT_PRO.md").decode())

    def test_duplicate_basenames_are_not_silently_resolved(self):
        for folder in ("first", "second"):
            (self.root / folder).mkdir()
            (self.root / folder / "same.md").write_text(folder)
        self.request["files"] = ["first/same.md", "second/same.md"]
        self.request["question"] = "请比较same.md。"
        result, data = self.call()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ambiguous material reference", data["error"])
        self.request["question"] = "请比较first/same.md与second/same.md。"
        result, data = self.call()
        self.assertEqual(result.returncode, 0, data)
        self.assertIn("请比较source/first/same.md与source/second/same.md。", Path(data["prompt_file"]).read_text())

    def test_already_canonical_references_and_notes_in_files_remain_consistent(self):
        self.request["files"].append("facts.md")
        self.request["question"] = "阅读context/codex-session-notes.md及source/evidence.md；facts.md是背景。"
        result, data = self.call()
        self.assertEqual(result.returncode, 0, data)
        prompt = Path(data["prompt_file"]).read_text()
        self.assertIn("阅读context/codex-session-notes.md及source/evidence.md；context/codex-session-notes.md是背景。", prompt)
        self.assertNotIn("source/source/", prompt)

    def test_existing_prompt_is_not_overwritten(self):
        path = self.root / "bundle.prompt.md"
        path.write_text("preserve")
        result, _data = self.call("--out", str(self.root / "bundle.zip"))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(path.read_text(), "preserve")
        self.assertFalse((self.root / "bundle.zip").exists())

    def test_existing_output_not_overwritten(self):
        file = self.root / "existing.zip"
        file.write_bytes(b"original evidence")
        result, _data = self.call("--out", str(file))
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(file.read_bytes(), b"original evidence")

    def test_stage_failure_keeps_verified_bundle(self):
        env = {**os.environ, "CODEX_BRIDGE_STAGING_WSL_ROOT": str(self.root / "missing")}
        result, data = self.call("--stage", env=env)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(data["phase"], "stage")
        self.assertFalse(data["sent"])
        with zipfile.ZipFile(data["bundle"]) as archive:
            self.assertIsNone(archive.testzip())

    def test_successful_stage_uses_matching_bundle(self):
        stage = self.root / "stage"
        stage.mkdir()
        env = {**os.environ, "CODEX_BRIDGE_STAGING_WSL_ROOT": str(stage),
               "CODEX_BRIDGE_STAGING_WINDOWS_ROOT": "C:\\BridgeStaging",
               "CODEX_BRIDGE_STAGING_LOCK": str(self.root / "staging.lock")}
        result, data = self.call("--stage", env=env)
        self.assertEqual(result.returncode, 0, data)
        self.assertEqual(Path(data["bundle"]).read_bytes(), Path(data["staging"]["staged_wsl_path"]).read_bytes())
        self.assertEqual(data["bundle_sha256"], data["staging"]["source_sha256"])

    def test_none_context_builds_zero_source_bundle_without_attachment_guide(self):
        self.request["context_policy"] = "none"
        self.request["max_files"] = 0
        self.request["files"] = []
        result, data = self.call()
        self.assertEqual(result.returncode, 0, data)
        self.assertTrue(data["ready"])
        self.assertEqual(data["context_policy"], "none")
        self.assertEqual(data["max_files"], 0)
        self.assertEqual(data["attachment_policy"], "none")
        self.assertNotIn("bundle", data)
        prompt = Path(data["prompt_file"]).read_text()
        self.assertIn("本轮不附加仓库文件", prompt)
        self.assertNotIn("附件阅读路径", prompt)
        self.assertIn("仅使用问题和对话上下文", prompt)
        self.assertFalse((self.root / ".codex" / "codex-pro-bridge" / "bundles").exists())

    def test_none_context_cannot_stage_a_browser_attachment(self):
        self.request["context_policy"] = "none"
        self.request["max_files"] = 0
        self.request["files"] = []
        result, data = self.call("--stage")
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(data["phase"], "request")
        self.assertIn("no browser attachment", data["error"])

    def test_none_context_rejects_bundle_output_path(self):
        self.request["context_policy"] = "none"
        self.request["max_files"] = 0
        self.request["files"] = []
        result, data = self.call("--out", str(self.root / "should-not-exist.zip"))
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot create a bundle", data["error"])
        self.assertFalse((self.root / "should-not-exist.zip").exists())

    def test_auto_context_still_builds_and_uses_bundle(self):
        self.request["context_policy"] = "auto"
        self.request["max_files"] = 3
        self.request["files"] = []
        result, data = self.call()
        self.assertEqual(result.returncode, 0, data)
        self.assertTrue(data["ready"])
        self.assertEqual(data["context_policy"], "auto")
        self.assertEqual(data["max_files"], 3)
        self.assertEqual(data["attachment_policy"], "bundle")
        self.assertIn("bundle", data)

    def test_context_policy_rejects_contradictory_file_lists(self):
        self.request["context_policy"] = "none"
        self.request["max_files"] = 0
        result, data = self.call()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be combined", data["error"])
        self.request["context_policy"] = "explicit"
        self.request["files"] = []
        self.request["max_files"] = 0
        result, data = self.call()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires at least one", data["error"])

    def test_legacy_builder_default_keeps_git_contract(self):
        result = subprocess.run(
            [
                sys.executable,
                str(BUILDER),
                "--repo",
                str(self.root),
                "--bridge-thread-id",
                "fixture-legacy",
                "--goal",
                "fixture",
                "--codex-session-notes",
                "facts.md",
                "--repo-context",
                "explicit",
                "--include",
                "evidence.md",
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(result.stdout.strip()) as archive:
            self.assertIn("context/git-status.txt", archive.namelist())


if __name__ == "__main__":
    unittest.main()
