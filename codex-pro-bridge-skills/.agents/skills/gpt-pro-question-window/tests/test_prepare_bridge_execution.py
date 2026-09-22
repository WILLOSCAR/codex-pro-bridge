from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "prepare_bridge_execution.py"
PREPARE_REVIEW = Path(__file__).resolve().parents[1] / "scripts" / "prepare_review.py"
SHARED = SKILLS / ".shared"
sys.path.insert(0, str(SHARED))

from project_store import BridgeProjectStore

REMOTE_A = "g-p-" + "a" * 32
REMOTE_B = "g-p-" + "b" * 32


class PrepareBridgeExecutionTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name).resolve()
        self.question = self.repo / "question.md"
        self.question.write_text("请检查证据边界。\n", encoding="utf-8")
        self.notes = self.repo / "notes.md"
        self.notes.write_text("Codex notes\n", encoding="utf-8")
        self.evidence = self.repo / "evidence.md"
        self.evidence.write_text("evidence\n", encoding="utf-8")
        self.store = BridgeProjectStore(self.repo)
        self.store.create_project("research", title="Research")
        self.store.bind_remote(
            "research",
            remote_url=f"https://chatgpt.com/g/{REMOTE_A}-old/project",
            remote_project_id=REMOTE_A,
            workspace="workspace",
            account_label="account",
            verified=True,
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def call(
        self,
        *extra: str,
        files: tuple[str, ...] | None = None,
        context_policy: str = "explicit",
        max_files: int = 24,
    ):
        selected_files = ("evidence.md",) if files is None else files
        args = [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(self.repo),
            "--goal",
            "检查 Bridge 准备合同",
            "--question-file",
            str(self.question),
            "--notes",
            "notes.md",
            "--context-policy",
            context_policy,
            "--max-files",
            str(max_files),
            "--requested-model",
            "最新",
            "--model-selection-kind",
            "latest-alias",
            "--requested-thinking-intensity",
            "高",
            "--allow-send",
        ]
        for file in selected_files:
            args.extend(["--file", file])
        args.extend(extra)
        return subprocess.run(args, capture_output=True, text=True, check=False)

    def test_explicit_target_rebinds_and_derives_thread(self) -> None:
        result = self.call(
            "--target-project-url",
            f"https://chatgpt.com/g/{REMOTE_B}-target/project",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["binding_action"], "rebind-and-verify")
        self.assertEqual(receipt["remote_project_id"], REMOTE_B)
        self.assertNotIn("real-call", receipt["bridge_thread_id"])
        handoff = json.loads(Path(receipt["handoff_file"]).read_text())
        self.assertEqual(handoff["schema_version"], "executor_handoff/v2")
        self.assertEqual(handoff["requested_thinking_intensity"], "高")
        self.assertEqual(handoff["bridge_thread_id"], receipt["bridge_thread_id"])
        binding = self.store.load_binding("research")
        self.assertEqual(binding["remote_project_id"], REMOTE_B)
        self.assertEqual(binding["status"], "unverified")
        manifest = json.loads(self.store.source_manifest_path("research").read_text())
        self.assertEqual(manifest["inventory_state"], "unverified")

    def test_missing_model_controls_fails_before_rebind(self) -> None:
        args = [
            sys.executable,
            str(SCRIPT),
            "--repo",
            str(self.repo),
            "--goal",
            "goal",
            "--question-file",
            str(self.question),
            "--notes",
            "notes.md",
            "--file",
            "evidence.md",
            "--requested-model",
            "最新",
            "--model-selection-kind",
            "latest-alias",
            "--allow-send",
            "--target-project-url",
            f"https://chatgpt.com/g/{REMOTE_B}-target/project",
        ]
        result = subprocess.run(args, capture_output=True, text=True, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requested-thinking-intensity", result.stderr)
        self.assertEqual(self.store.load_binding("research")["remote_project_id"], REMOTE_A)

    def test_wrong_project_url_is_rejected_without_output(self) -> None:
        result = self.call("--target-project-url", "https://example.com/g-p-invalid/project")
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse((self.repo / ".codex" / "codex-pro-bridge" / "executor-preparations").exists())

    def test_target_url_and_explicit_remote_id_must_agree(self) -> None:
        result = self.call(
            "--target-project-url",
            f"https://chatgpt.com/g/{REMOTE_B}-target/project",
            "--remote-project-id",
            REMOTE_A,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("identifies", result.stderr)
        self.assertEqual(self.store.load_binding("research")["remote_project_id"], REMOTE_A)

    def test_same_inputs_are_idempotent(self) -> None:
        extra = [
            "--target-project-url",
            f"https://chatgpt.com/g/{REMOTE_B}-target/project",
        ]
        first = self.call(*extra)
        second = self.call(*extra)
        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertEqual(json.loads(first.stdout)["handoff_sha256"], json.loads(second.stdout)["handoff_sha256"])

    def test_tampered_receipt_is_not_reused(self) -> None:
        first = self.call("--standalone")
        self.assertEqual(first.returncode, 0, first.stderr)
        receipt = json.loads(first.stdout)
        receipt_path = Path(receipt["request_file"]).with_name("receipt.json")
        tampered = json.loads(receipt_path.read_text(encoding="utf-8"))
        tampered["handoff_sha256"] = "0" * 64
        receipt_path.write_text(
            json.dumps(tampered, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        second = self.call("--standalone")
        self.assertNotEqual(second.returncode, 0)
        self.assertIn("receipt conflicts with handoff", second.stderr)

    def test_rebind_does_not_reuse_old_project_task(self) -> None:
        old_task = self.store.attach_thread(
            "research",
            "old-project-task",
            title="检查 Bridge 准备合同",
            goal="检查 Bridge 准备合同",
        )
        result = self.call(
            "--target-project-url",
            f"https://chatgpt.com/g/{REMOTE_B}-target/project",
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertNotEqual(receipt["bridge_thread_id"], old_task["bridge_thread_id"])

    def test_handoff_v1_prepare_is_not_emitted(self) -> None:
        result = self.call("--standalone")
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        handoff = json.loads(Path(receipt["handoff_file"]).read_text())
        self.assertEqual(handoff["schema_version"], "executor_handoff/v2")
        self.assertEqual(handoff["binding_action"], "none")

    def test_without_explicit_target_reuses_current_binding(self) -> None:
        result = self.call()
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.assertEqual(receipt["binding_action"], "reuse")
        self.assertEqual(receipt["remote_project_id"], REMOTE_A)

    def test_none_policy_allows_empty_files_and_no_upload_action(self) -> None:
        result = self.call(
            "--standalone",
            context_policy="none",
            files=(),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        handoff = json.loads(Path(receipt["handoff_file"]).read_text())
        request = json.loads(Path(receipt["request_file"]).read_text())
        self.assertEqual(request["context_policy"], "none")
        self.assertEqual(request["files"], [])
        self.assertEqual(request["max_files"], 0)
        self.assertEqual(handoff["context_policy"], "none")
        self.assertEqual(handoff["max_files"], 0)
        self.assertEqual(handoff["attachment_policy"], "none")
        self.assertNotIn("upload-task-bundle", handoff["allowed_external_actions"])
        self.assertIn("send-once", handoff["allowed_external_actions"])
        self.assertIn("capture-reply", handoff["allowed_external_actions"])
        self.assertEqual(handoff["expected_output_dir"], str(Path(receipt["handoff_file"]).parent))

    def test_none_policy_with_file_fails_before_project_rebind(self) -> None:
        result = self.call(
            "--target-project-url",
            f"https://chatgpt.com/g/{REMOTE_B}-target/project",
            context_policy="none",
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be combined", result.stderr)
        self.assertEqual(self.store.load_binding("research")["remote_project_id"], REMOTE_A)

    def test_explicit_policy_without_file_fails_before_project_rebind(self) -> None:
        result = self.call(
            "--target-project-url",
            f"https://chatgpt.com/g/{REMOTE_B}-target/project",
            context_policy="explicit",
            files=(),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires at least one", result.stderr)
        self.assertEqual(self.store.load_binding("research")["remote_project_id"], REMOTE_A)

    def test_auto_policy_freezes_positive_max_files(self) -> None:
        result = self.call(
            "--standalone",
            context_policy="auto",
            files=(),
            max_files=3,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        request = json.loads(Path(receipt["request_file"]).read_text())
        handoff = json.loads(Path(receipt["handoff_file"]).read_text())
        self.assertEqual(request["context_policy"], "auto")
        self.assertEqual(request["max_files"], 3)
        self.assertEqual(handoff["context_policy"], "auto")
        self.assertEqual(handoff["max_files"], 3)
        self.assertEqual(handoff["attachment_policy"], "bundle")
        self.assertIn("upload-task-bundle", handoff["allowed_external_actions"])

    def test_auto_policy_rejects_max_files_below_explicit_seed_count(self) -> None:
        result = self.call(
            "--standalone",
            context_policy="auto",
            files=("evidence.md", "notes.md"),
            max_files=1,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("smaller than the explicit file count", result.stderr)
        self.assertEqual(self.store.load_binding("research")["remote_project_id"], REMOTE_A)

    def test_material_drift_is_rejected_before_bundle_build(self) -> None:
        result = self.call()
        self.assertEqual(result.returncode, 0, result.stderr)
        receipt = json.loads(result.stdout)
        self.evidence.write_text("drifted evidence\n", encoding="utf-8")
        checked = subprocess.run(
            [
                sys.executable,
                str(PREPARE_REVIEW),
                "--request",
                receipt["request_file"],
            ],
            capture_output=True,
            text=True,
            check=False,
        )
        self.assertNotEqual(checked.returncode, 0)
        self.assertIn("file drift detected", checked.stdout)


if __name__ == "__main__":
    unittest.main()
