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

    def capture(self, bundle: Path, thread: str = "demo-task", session: str = "") -> Path:
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
