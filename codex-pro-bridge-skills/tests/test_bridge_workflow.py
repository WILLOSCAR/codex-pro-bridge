from __future__ import annotations

import json
import os
import subprocess
import tempfile
import unittest
import zipfile
from pathlib import Path


PACKAGE = Path(__file__).resolve().parents[1]
SKILLS = PACKAGE / ".agents" / "skills"
PREPARE = SKILLS / "bundle-algorithm-context" / "scripts" / "prepare_codex_session_notes.py"
BUILD = SKILLS / "bundle-algorithm-context" / "scripts" / "build_algorithm_bundle.py"
SAVE = SKILLS / "gpt-pro-question-window" / "scripts" / "save_bridge_turn.py"
VERDICT = SKILLS / "gpt-pro-question-window" / "scripts" / "record_codex_verdict.py"
PREFLIGHT = SKILLS / "gpt-pro-question-window" / "scripts" / "check_browser_preflight.py"
VERIFY_THREAD = SKILLS / "gpt-pro-question-window" / "scripts" / "verify_bridge_thread.py"


class BridgeWorkflowTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory(prefix="codex-pro-bridge-test-")
        self.repo = Path(self.temp.name) / "repo"
        self.repo.mkdir()
        self.run_cmd(["git", "init", "-q"])
        (self.repo / "README.md").write_text("# Demo\n", encoding="utf-8")
        self.run_cmd(["git", "add", "README.md"])

    def tearDown(self) -> None:
        self.temp.cleanup()

    def run_cmd(self, command: list[str], *, check: bool = True, env: dict[str, str] | None = None):
        result = subprocess.run(
            command,
            cwd=self.repo,
            text=True,
            capture_output=True,
            env=env,
        )
        if check and result.returncode:
            self.fail(
                f"command failed: {command}\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
            )
        return result

    def prepare(self, thread: str = "demo-task", session: str = "") -> Path:
        command = [
            "python3",
            str(PREPARE),
            "--repo",
            str(self.repo),
            "--bridge-thread-id",
            thread,
            "--goal",
            "Review the demo",
            "--summary",
            "Current state and decision context",
        ]
        if session:
            command.extend(["--codex-session-id", session])
        result = self.run_cmd(command)
        return Path(result.stdout.strip())

    def build(self, output: str = "demo.zip", **options: str) -> Path:
        command = [
            "python3",
            str(BUILD),
            "--repo",
            str(self.repo),
            "--bridge-thread-id",
            options.pop("thread", "demo-task"),
            "--goal",
            "Review the demo",
            "--question",
            options.pop("question", "What should change?"),
            "--mode",
            options.pop("mode", "general_question"),
            "--format",
            options.pop("format", "zip"),
            "--repo-context",
            options.pop("repo_context", "auto"),
            "--out",
            str(self.repo / ".codex" / "codex-pro-bridge" / "bundles" / output),
        ]
        for key, value in options.items():
            flag = "--" + key.replace("_", "-")
            if value == "true":
                command.append(flag)
            else:
                command.extend([flag, value])
        result = self.run_cmd(command)
        return Path(result.stdout.strip())

    def capture(
        self,
        bundle: Path,
        thread: str = "demo-task",
        session: str = "",
        **options: str,
    ) -> Path:
        command = [
            "python3",
            str(SAVE),
            "--repo",
            str(self.repo),
            "--bridge-thread-id",
            thread,
            "--web-url",
            "https://chatgpt.com/c/demo",
            "--web-title",
            "Demo Review",
            "--purpose",
            "Review demo",
            "--bundle",
            str(bundle),
            "--prompt",
            "What should change?",
            "--answer",
            "Change one thing.",
        ]
        if session:
            command.extend(["--gpt-pro-session-id", session])
        for key, value in options.items():
            command.extend(["--" + key.replace("_", "-"), value])
        result = self.run_cmd(command)
        return Path(result.stdout.strip())

    def events(self, thread: str = "demo-task") -> list[dict]:
        path = self.repo / ".codex" / "codex-pro-bridge" / "threads" / f"{thread}.jsonl"
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]

    def test_complete_round_has_three_immutable_events(self) -> None:
        notes = self.prepare()
        bundle = self.build()
        turn = self.capture(bundle)
        result = self.run_cmd(
            [
                "python3",
                str(VERDICT),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
                "--turn",
                str(turn),
                "--summary",
                "One proposed change",
                "--verification",
                "Verified against README.md",
                "--decision-trail",
                "Accepted",
            ]
        )
        verdict = Path(result.stdout.strip())
        self.assertTrue(notes.is_file())
        self.assertTrue(bundle.is_file())
        self.assertTrue(turn.is_file())
        self.assertTrue(verdict.is_file())
        events = self.events()
        self.assertEqual(
            [event["event_type"] for event in events],
            ["codex-snapshot", "gpt-exchange", "codex-verdict"],
        )
        self.assertEqual(events[1]["parent_event_id"], events[0]["event_id"])
        self.assertEqual(events[2]["parent_event_id"], events[1]["event_id"])
        self.assertEqual(events[1]["data"]["bundle_sha256"], self.sha256(bundle))
        timeline = (
            self.repo / ".codex" / "codex-pro-bridge" / "threads" / "demo-task.md"
        ).read_text(encoding="utf-8")
        self.assertIn("sequenceDiagram", timeline)
        self.assertNotIn("gitGraph", timeline)
        context = self.run_cmd(
            [
                "python3",
                "-c",
                (
                    "import sys; from pathlib import Path; "
                    f"sys.path.insert(0, {str(SKILLS / '.shared')!r}); "
                    "from bridge_store import compact_thread_context; "
                    "print(compact_thread_context(Path(sys.argv[1]), 'demo-task'))"
                ),
                str(self.repo),
            ]
        ).stdout
        self.assertIn(f"Event ID: `{events[1]['event_id']}`", context)
        self.assertIn(f"Parent: `{events[0]['event_id']}`", context)

    def test_thread_verifier_detects_artifact_tampering(self) -> None:
        self.prepare()
        bundle = self.build()
        turn = self.capture(bundle)
        self.run_cmd(
            [
                "python3",
                str(VERDICT),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
                "--turn",
                str(turn),
                "--summary",
                "One proposed change",
                "--verification",
                "Verified locally",
            ]
        )
        verified = self.run_cmd(
            [
                "python3",
                str(VERIFY_THREAD),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
                "--require-complete-rounds",
                "--json",
            ]
        )
        report = json.loads(verified.stdout)
        self.assertTrue(report["valid"])
        self.assertEqual(report["event_count"], 3)
        self.assertEqual(report["complete_rounds"], 1)

        turn.write_text(turn.read_text(encoding="utf-8") + "tampered\n", encoding="utf-8")
        tampered = self.run_cmd(
            [
                "python3",
                str(VERIFY_THREAD),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
            ],
            check=False,
        )
        self.assertNotEqual(tampered.returncode, 0)
        self.assertIn("artifact hash mismatch", tampered.stderr)

    def test_thread_verifier_detects_parent_chain_tampering(self) -> None:
        self.prepare()
        bundle = self.build()
        self.capture(bundle)
        ledger = (
            self.repo
            / ".codex"
            / "codex-pro-bridge"
            / "threads"
            / "demo-task.jsonl"
        )
        events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        events[1]["parent_event_id"] = "wrong-parent"
        ledger.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
            encoding="utf-8",
        )
        result = self.run_cmd(
            [
                "python3",
                str(VERIFY_THREAD),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
            ],
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("parent chain mismatch", result.stderr)

    def test_thread_verifier_detects_event_role_tampering(self) -> None:
        self.prepare()
        bundle = self.build()
        self.capture(bundle)
        ledger = (
            self.repo
            / ".codex"
            / "codex-pro-bridge"
            / "threads"
            / "demo-task.jsonl"
        )
        events = [json.loads(line) for line in ledger.read_text(encoding="utf-8").splitlines()]
        events[1]["actor"] = "codex"
        ledger.write_text(
            "".join(json.dumps(event, sort_keys=True) + "\n" for event in events),
            encoding="utf-8",
        )
        result = self.run_cmd(
            [
                "python3",
                str(VERIFY_THREAD),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
            ],
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("actor mismatch", result.stderr)

    def test_capture_and_verdict_retries_are_idempotent(self) -> None:
        self.prepare()
        bundle = self.build()
        first_turn = self.capture(bundle)
        second_turn = self.capture(bundle)
        self.assertEqual(first_turn, second_turn)
        self.assertEqual(len(list(first_turn.parent.glob("[0-9]*-*.md"))), 1)

        command = [
            "python3",
            str(VERDICT),
            "--repo",
            str(self.repo),
            "--bridge-thread-id",
            "demo-task",
            "--turn",
            str(first_turn),
            "--summary",
            "Same summary",
            "--verification",
            "Same verification",
        ]
        first_verdict = Path(self.run_cmd(command).stdout.strip())
        second_verdict = Path(self.run_cmd(command).stdout.strip())
        self.assertEqual(first_verdict, second_verdict)
        self.assertEqual(len(list(first_verdict.parent.glob("*.md"))), 1)
        self.assertEqual(
            [event["event_type"] for event in self.events()],
            ["codex-snapshot", "gpt-exchange", "codex-verdict"],
        )

    def test_verdict_rejects_turn_outside_bound_gpt_session(self) -> None:
        self.prepare()
        bundle = self.build()
        self.capture(bundle)
        unrelated = self.repo / "not-a-captured-turn.md"
        unrelated.write_text("# Not a captured turn\n", encoding="utf-8")
        result = self.run_cmd(
            [
                "python3",
                str(VERDICT),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
                "--turn",
                str(unrelated),
                "--summary",
                "invalid",
                "--verification",
                "invalid",
            ],
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("bound GPT Pro session", result.stderr)

    def test_long_thread_id_derives_bounded_session_ids(self) -> None:
        thread = "a" * 80
        self.prepare(thread=thread)
        sessions = list(
            (self.repo / ".codex" / "codex-pro-bridge" / "codex-sessions").glob("*/session.md")
        )
        self.assertEqual(len(sessions), 1)
        self.assertLessEqual(len(sessions[0].parent.name), 80)

    def test_concurrent_event_appends_do_not_lose_updates(self) -> None:
        shared = SKILLS / ".shared"
        code = (
            "import sys; from pathlib import Path; "
            f"sys.path.insert(0, {str(shared)!r}); "
            "from bridge_store import append_event; "
            "append_event(Path(sys.argv[1]), thread_id='concurrent-task', "
            "event_type='codex-snapshot', actor='codex', "
            "data={'summary': sys.argv[2]}, dedupe_key='event:'+sys.argv[2])"
        )
        processes = [
            subprocess.Popen(
                ["python3", "-c", code, str(self.repo), str(index)],
                cwd=self.repo,
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
            )
            for index in range(8)
        ]
        failures = []
        for process in processes:
            stdout, stderr = process.communicate(timeout=20)
            if process.returncode:
                failures.append((stdout, stderr))
        self.assertFalse(failures, failures)
        events = self.events("concurrent-task")
        self.assertEqual(len(events), 8)
        self.assertEqual(len({event["event_id"] for event in events}), 8)
        for index, event in enumerate(events):
            expected_parent = events[index - 1]["event_id"] if index else ""
            self.assertEqual(event["parent_event_id"], expected_parent)

    def test_bundle_build_does_not_add_event_and_refuses_overwrite(self) -> None:
        self.prepare()
        bundle = self.build()
        self.assertEqual(len(self.events()), 1)
        command = [
            "python3",
            str(BUILD),
            "--repo",
            str(self.repo),
            "--bridge-thread-id",
            "demo-task",
            "--goal",
            "Review",
            "--format",
            "zip",
            "--out",
            str(bundle),
        ]
        result = self.run_cmd(command, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Refusing to overwrite immutable artifact", result.stderr)
        self.assertEqual(len(self.events()), 1)

    def test_notes_updates_keep_immutable_snapshots_and_created_time(self) -> None:
        notes = self.prepare()
        first = notes.read_text(encoding="utf-8")
        self.prepare()
        second = notes.read_text(encoding="utf-8")
        snapshots = sorted((notes.parent / "snapshots").glob("*.md"))
        self.assertEqual(len(snapshots), 2)
        self.assertIn("History source: unavailable", second)
        first_created = next(line for line in first.splitlines() if line.startswith("- Created at:"))
        second_created = next(line for line in second.splitlines() if line.startswith("- Created at:"))
        self.assertEqual(first_created, second_created)
        self.assertEqual(
            [event["event_type"] for event in self.events()],
            ["codex-snapshot", "codex-snapshot"],
        )

    def test_bundle_uses_latest_immutable_notes_snapshot(self) -> None:
        notes = self.prepare()
        notes.write_text("MUTATED CURRENT NOTES\n", encoding="utf-8")
        bundle = self.build()
        with zipfile.ZipFile(bundle) as archive:
            bundled_notes = archive.read("context/codex-session-notes.md").decode("utf-8")
        self.assertNotIn("MUTATED CURRENT NOTES", bundled_notes)
        self.assertIn("Current state and decision context", bundled_notes)

    def test_codex_and_gpt_sessions_cannot_rebind(self) -> None:
        self.prepare(thread="thread-a", session="fixed-codex")
        result = self.run_cmd(
            [
                "python3",
                str(PREPARE),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "thread-b",
                "--codex-session-id",
                "fixed-codex",
                "--summary",
                "new summary",
            ],
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already bound", result.stderr)

        self.prepare(thread="thread-a")
        self.prepare(thread="thread-b")
        bundle_a = self.build(output="a.zip", thread="thread-a")
        self.capture(bundle_a, thread="thread-a", session="fixed-gpt")
        bundle_b = self.build(output="b.zip", thread="thread-b")
        result = self.run_cmd(
            [
                "python3",
                str(SAVE),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "thread-b",
                "--gpt-pro-session-id",
                "fixed-gpt",
                "--web-url",
                "https://chatgpt.com/c/demo",
                "--bundle",
                str(bundle_b),
                "--prompt",
                "p",
                "--answer",
                "a",
            ],
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("already bound", result.stderr)

    def test_gpt_session_cannot_change_web_conversation(self) -> None:
        self.prepare()
        bundle = self.build()
        self.capture(bundle)
        result = self.run_cmd(
            [
                "python3",
                str(SAVE),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
                "--web-url",
                "https://chatgpt.com/c/another",
                "--bundle",
                str(bundle),
                "--prompt",
                "different prompt",
                "--answer",
                "different answer",
            ],
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("cannot be rebound", result.stderr)

    def test_capture_rejects_non_chatgpt_web_url(self) -> None:
        self.prepare()
        bundle = self.build()
        result = self.run_cmd(
            [
                "python3",
                str(SAVE),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
                "--web-url",
                "https://example.com/c/demo",
                "--bundle",
                str(bundle),
                "--prompt",
                "p",
                "--answer",
                "a",
            ],
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("ChatGPT conversation", result.stderr)

    def test_capture_persists_exact_model_and_browser_timing_provenance(self) -> None:
        self.prepare()
        bundle = self.build()
        turn = self.capture(
            bundle,
            requested_model="Pro",
            selected_ui_label="Pro",
            attachment_name=bundle.name,
            upload_control="visible-menu",
            submitted_at="2026-07-10T10:00:00+08:00",
            generation_observed_at="2026-07-10T10:00:05+08:00",
            response_completed_at="2026-07-10T10:12:00+08:00",
        )
        text = turn.read_text(encoding="utf-8")
        event = self.events()[-1]
        self.assertIn("Requested Model: Pro", text)
        self.assertIn("Selected UI Label: Pro", text)
        self.assertIn("Model Verification: verified", text)
        self.assertIn("Upload Control: visible-menu", text)
        self.assertEqual(event["data"]["model_verification"], "verified")
        self.assertEqual(event["data"]["requested_model"], "Pro")
        self.assertEqual(event["data"]["selected_ui_label"], "Pro")
        self.assertEqual(event["data"]["response_wait_seconds"], 720)

    def test_capture_records_model_mismatch_without_claiming_pro(self) -> None:
        self.prepare()
        bundle = self.build()
        turn = self.capture(
            bundle,
            requested_model="Pro",
            selected_ui_label="极高",
        )
        text = turn.read_text(encoding="utf-8")
        event = self.events()[-1]
        self.assertIn("Model Verification: mismatch", text)
        self.assertEqual(event["data"]["model_verification"], "mismatch")

    def test_browser_preflight_gates_exact_model_and_attachment(self) -> None:
        self.prepare()
        bundle = self.build()
        ready = self.run_cmd(
            [
                "python3",
                str(PREFLIGHT),
                "--requested-model",
                "Pro",
                "--selected-ui-label",
                "Pro",
                "--bundle",
                str(bundle),
                "--attachment-name",
                bundle.name,
                "--upload-control",
                "visible-menu",
            ]
        )
        report = json.loads(ready.stdout)
        self.assertTrue(report["ready"])
        self.assertEqual(report["model_verification"], "verified")
        self.assertEqual(report["attachment_verification"], "verified")

        wrong_model = self.run_cmd(
            [
                "python3",
                str(PREFLIGHT),
                "--requested-model",
                "Pro",
                "--selected-ui-label",
                "极高",
            ],
            check=False,
        )
        self.assertNotEqual(wrong_model.returncode, 0)
        self.assertIn("does not exactly match", wrong_model.stderr)

        relative_bundle = self.run_cmd(
            [
                "python3",
                str(PREFLIGHT),
                "--requested-model",
                "Pro",
                "--selected-ui-label",
                "Pro",
                "--bundle",
                ".codex/codex-pro-bridge/bundles/demo.zip",
                "--attachment-name",
                bundle.name,
                "--upload-control",
                "visible-menu",
            ],
            check=False,
        )
        self.assertNotEqual(relative_bundle.returncode, 0)
        self.assertIn("absolute path", relative_bundle.stderr)

    def test_missing_and_external_includes_fail_closed(self) -> None:
        self.prepare()
        missing = self.run_cmd(
            [
                "python3",
                str(BUILD),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
                "--goal",
                "Review",
                "--repo-context",
                "explicit",
                "--include",
                "missing.py",
            ],
            check=False,
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("include did not match", missing.stderr)

        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("outside evidence\n", encoding="utf-8")
        escaped = self.run_cmd(
            [
                "python3",
                str(BUILD),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
                "--goal",
                "Review",
                "--repo-context",
                "explicit",
                "--include",
                str(outside),
            ],
            check=False,
        )
        self.assertNotEqual(escaped.returncode, 0)
        self.assertIn("escapes repository root", escaped.stderr)

    def test_explicit_bundle_includes_modern_node_module_files(self) -> None:
        self.prepare()
        test_file = self.repo / "tests" / "run-context.test.mjs"
        test_file.parent.mkdir()
        test_file.write_text("import assert from 'node:assert/strict';\n", encoding="utf-8")

        bundle = self.build(
            output="modern-node.zip",
            repo_context="explicit",
            include="tests/run-context.test.mjs",
        )

        with zipfile.ZipFile(bundle) as archive:
            self.assertIn("source/tests/run-context.test.mjs", archive.namelist())

    def test_explicit_bundle_rejects_policy_excluded_requested_file(self) -> None:
        self.prepare()
        (self.repo / ".env").write_text("SAFE_TEST_VALUE=1\n", encoding="utf-8")
        result = self.run_cmd(
            [
                "python3",
                str(BUILD),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
                "--goal",
                "Review",
                "--repo-context",
                "explicit",
                "--include",
                ".env",
            ],
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("Explicit evidence is incomplete", result.stderr)
        self.assertIn("excluded by path, name, extension, or binary policy", result.stderr)

    def test_explicit_context_requires_at_least_one_include(self) -> None:
        self.prepare()
        result = self.run_cmd(
            [
                "python3",
                str(BUILD),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
                "--goal",
                "Review",
                "--repo-context",
                "explicit",
            ],
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("requires --include", result.stderr)

    def test_approved_external_include_is_anonymized(self) -> None:
        self.prepare()
        outside = Path(self.temp.name) / "outside.md"
        outside.write_text("approved external evidence\n", encoding="utf-8")
        bundle = self.build(
            output="external.zip",
            repo_context="explicit",
            include=str(outside),
            allow_external_include="true",
        )
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            manifest = archive.read("README_FOR_GPT_PRO.md").decode("utf-8")
        external_names = [name for name in names if name.startswith("source/external/")]
        self.assertEqual(len(external_names), 1)
        self.assertNotIn(str(outside.parent), manifest)
        self.assertNotIn(str(self.repo), manifest)

    def test_auto_selection_does_not_follow_repository_symlink_outside_root(self) -> None:
        self.prepare()
        outside = Path(self.temp.name) / "important_reward_model.py"
        outside.write_text("OUTSIDE_SECRET = 1\n", encoding="utf-8")
        link = self.repo / "important_reward_model.py"
        link.symlink_to(outside)
        self.run_cmd(["git", "add", "important_reward_model.py"])
        bundle = self.build(question="Review important reward model")
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
        self.assertNotIn("source/important_reward_model.py", names)
        self.assertFalse(any(name.startswith("source/external/") for name in names))

    def test_auto_selection_includes_untracked_relevant_file(self) -> None:
        self.prepare()
        (self.repo / "important_reward_model.py").write_text(
            "def reward_model():\n    return 1\n", encoding="utf-8"
        )
        bundle = self.build(question="Review important reward model")
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            manifest = archive.read("README_FOR_GPT_PRO.md").decode("utf-8")
        self.assertIn("source/important_reward_model.py", names)
        self.assertNotIn(str(self.repo), manifest)

    def test_auto_selection_closes_local_source_dependencies_before_breadth(self) -> None:
        self.prepare()
        source = self.repo / "src"
        source.mkdir()
        (source / "types.ts").write_text("export type Run = { id: string };\n", encoding="utf-8")
        (source / "state.ts").write_text(
            "import type { Run } from './types.js';\nexport const run: Run = { id: 'a' };\n",
            encoding="utf-8",
        )
        focus = source / "focus.ts"
        focus.write_text(
            "import { run } from './state.js';\nexport const current = run;\n",
            encoding="utf-8",
        )
        self.run_cmd(["git", "add", "README.md", "src"])
        self.run_cmd(
            [
                "git",
                "-c",
                "user.name=Bridge Test",
                "-c",
                "user.email=bridge@example.com",
                "commit",
                "-qm",
                "fixture",
            ]
        )
        focus.write_text(
            "import { run } from './state.js';\nexport const current = { ...run };\n",
            encoding="utf-8",
        )

        bundle = self.build(
            output="closure.zip",
            question="Review the focus implementation",
            max_files="3",
        )

        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            manifest = archive.read("README_FOR_GPT_PRO.md").decode("utf-8")
        self.assertIn("source/src/focus.ts", names)
        self.assertIn("source/src/state.ts", names)
        self.assertIn("source/src/types.ts", names)
        self.assertIn("Auto dependency closure: `complete`", manifest)
        self.assertIn("dependency of src/focus.ts", manifest)
        self.assertIn("dependency of src/state.ts", manifest)

    def test_auto_selection_fails_when_primary_dependency_closure_exceeds_budget(self) -> None:
        self.prepare()
        source = self.repo / "src"
        source.mkdir()
        (source / "types.ts").write_text("export type Run = string;\n", encoding="utf-8")
        (source / "state.ts").write_text(
            "import type { Run } from './types.js';\nexport const run: Run = 'a';\n",
            encoding="utf-8",
        )
        (source / "focus.ts").write_text(
            "import { run } from './state.js';\nexport const current = run;\n",
            encoding="utf-8",
        )
        result = self.run_cmd(
            [
                "python3",
                str(BUILD),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
                "--goal",
                "Review focus",
                "--question",
                "Review focus",
                "--repo-context",
                "auto",
                "--include",
                "src/focus.ts",
                "--max-files",
                "2",
            ],
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("dependency closure", result.stderr)
        self.assertIn("exceeds --max-files", result.stderr)

    def test_auto_selection_recognizes_changed_paths_with_spaces(self) -> None:
        self.prepare()
        source = self.repo / "src"
        source.mkdir()
        focus = source / "focus file.ts"
        focus.write_text("export const current = 1;\n", encoding="utf-8")
        self.run_cmd(["git", "add", "README.md", "src"])
        self.run_cmd(
            [
                "git",
                "-c",
                "user.name=Bridge Test",
                "-c",
                "user.email=bridge@example.com",
                "commit",
                "-qm",
                "fixture",
            ]
        )
        focus.write_text("export const current = 2;\n", encoding="utf-8")
        bundle = self.build(
            output="space-path.zip",
            question="Review focus",
            max_files="1",
        )
        with zipfile.ZipFile(bundle) as archive:
            self.assertIn("source/src/focus file.ts", archive.namelist())

    def test_auto_selection_closes_relative_python_imports(self) -> None:
        self.prepare()
        package = self.repo / "pkg"
        package.mkdir()
        (package / "state.py").write_text("value = 1\n", encoding="utf-8")
        (package / "state.ts").write_text("export const value = 2;\n", encoding="utf-8")
        (package / "focus.py").write_text(
            "from .state import value\nresult = value\n", encoding="utf-8"
        )
        bundle = self.build(
            output="python-closure.zip",
            question="Review focus",
            include="pkg/focus.py",
            max_files="2",
        )
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            self.assertIn("source/pkg/focus.py", names)
            self.assertIn("source/pkg/state.py", names)
            self.assertNotIn("source/pkg/state.ts", names)

    def test_auto_selection_prioritizes_question_named_tests_over_unrelated_changes(self) -> None:
        self.prepare()
        source = self.repo / "src"
        tests = self.repo / "tests"
        source.mkdir()
        tests.mkdir()
        (source / "focus.ts").write_text("export const focus = true;\n", encoding="utf-8")
        (tests / "run-context.test.mjs").write_text(
            "import assert from 'node:assert/strict';\nassert.ok(true);\n",
            encoding="utf-8",
        )
        self.run_cmd(["git", "add", "README.md", "src", "tests"])
        self.run_cmd(
            [
                "git",
                "-c",
                "user.name=Bridge Test",
                "-c",
                "user.email=bridge@example.com",
                "commit",
                "-qm",
                "fixture",
            ]
        )
        for index in range(20):
            (source / f"unrelated-{index}.ts").write_text(
                f"export const value{index} = {index};\n", encoding="utf-8"
            )
        bundle = self.build(
            output="test-priority.zip",
            question="Review the run-context contract",
            include="src/focus.ts",
            max_files="3",
        )
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
        self.assertIn("source/src/focus.ts", names)
        self.assertIn("source/tests/run-context.test.mjs", names)

    def test_mode_specific_output_contract(self) -> None:
        self.prepare()
        general = self.build(output="general.zip", mode="general_question")
        implementation = self.build(output="implementation.zip", mode="implementation_check")
        with zipfile.ZipFile(general) as archive:
            general_manifest = archive.read("README_FOR_GPT_PRO.md").decode("utf-8")
        with zipfile.ZipFile(implementation) as archive:
            implementation_manifest = archive.read("README_FOR_GPT_PRO.md").decode("utf-8")
        self.assertIn("Direct Answer", general_manifest)
        self.assertNotIn("Ablation Matrix", general_manifest)
        self.assertIn("Concrete Mismatches", implementation_manifest)

    def test_none_context_accepts_zero_file_budget(self) -> None:
        self.prepare()
        bundle = self.build(
            output="notes-only.zip",
            repo_context="none",
            max_files="0",
        )
        with zipfile.ZipFile(bundle) as archive:
            names = archive.namelist()
            manifest = archive.read("README_FOR_GPT_PRO.md").decode("utf-8")
        self.assertFalse(any(name.startswith("source/") for name in names))
        self.assertIn("Repository context: `none`", manifest)

    def test_high_confidence_secret_pattern_fails(self) -> None:
        self.prepare()
        secret = self.repo / "config.md"
        secret.write_text("token: ghp_abcdefghijklmnopqrstuvwxyz1234567890\n", encoding="utf-8")
        result = self.run_cmd(
            [
                "python3",
                str(BUILD),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "demo-task",
                "--goal",
                "Review",
                "--repo-context",
                "explicit",
                "--include",
                "config.md",
            ],
            check=False,
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("secret-like content", result.stderr)

    def test_legacy_markdown_thread_migrates_on_next_write(self) -> None:
        bridge = self.repo / ".codex" / "codex-pro-bridge" / "threads"
        bridge.mkdir(parents=True)
        (bridge / "legacy-task.md").write_text(
            "bridge_thread_id: \"legacy-task\"\n"
            "title: \"Legacy\"\n"
            "created_at: \"2026-07-08T10:00:00\"\n\n"
            "# Bridge Thread: Legacy\n\n## Timeline\n\n"
            "### 2026-07-08T10:00:00 - codex-update\n\n- Summary: old\n",
            encoding="utf-8",
        )
        self.prepare(thread="legacy-task")
        events = self.events("legacy-task")
        self.assertEqual([event["event_type"] for event in events], ["codex-snapshot", "codex-snapshot"])
        self.assertTrue(events[0]["event_id"].startswith("legacy-"))
        rendered = (bridge / "legacy-task.md").read_text(encoding="utf-8")
        self.assertIn("sequenceDiagram", rendered)

    def test_installer_removes_managed_stale_files_only(self) -> None:
        home = Path(self.temp.name) / "codex-home"
        stale = home / "skills" / "gpt-pro-question-window" / "stale.txt"
        unrelated = home / "skills" / "user-skill" / "keep.txt"
        stale.parent.mkdir(parents=True)
        unrelated.parent.mkdir(parents=True)
        stale.write_text("stale", encoding="utf-8")
        unrelated.write_text("keep", encoding="utf-8")
        env = dict(os.environ)
        env["CODEX_HOME"] = str(home)
        result = subprocess.run(
            ["bash", str(PACKAGE / "install.sh"), "--global"],
            cwd=PACKAGE,
            text=True,
            capture_output=True,
            env=env,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertFalse(stale.exists())
        self.assertTrue(unrelated.exists())
        self.assertTrue((home / "skills" / ".shared" / "bridge_store.py").is_file())
        self.assertFalse(any((home / "skills").rglob("__pycache__")))
        self.assertFalse(any((home / "skills").rglob("*.pyc")))

    def test_repo_installer_excludes_local_skills_and_bridge_state(self) -> None:
        result = subprocess.run(
            ["bash", str(PACKAGE / "install.sh"), "--repo", str(self.repo)],
            cwd=PACKAGE,
            text=True,
            capture_output=True,
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        exclude = (self.repo / ".git" / "info" / "exclude").read_text(encoding="utf-8")
        self.assertIn(".agents/", exclude.splitlines())
        self.assertIn(".codex/", exclude.splitlines())
        (self.repo / ".codex" / "local.txt").parent.mkdir(parents=True, exist_ok=True)
        (self.repo / ".codex" / "local.txt").write_text("local", encoding="utf-8")
        status = self.run_cmd(["git", "status", "--short"]).stdout
        self.assertNotIn(".agents/", status)
        self.assertNotIn(".codex/", status)

    @staticmethod
    def sha256(path: Path) -> str:
        import hashlib

        return hashlib.sha256(path.read_bytes()).hexdigest()


if __name__ == "__main__":
    unittest.main()
