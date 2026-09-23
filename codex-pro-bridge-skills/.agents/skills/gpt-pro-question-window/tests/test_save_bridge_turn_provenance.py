from __future__ import annotations

import hashlib
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SKILLS = Path(__file__).resolve().parents[2]
SHARED = SKILLS / ".shared"
SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "save_bridge_turn.py"
sys.path.insert(0, str(SHARED))

from bridge_attempts import (
    mark_send_started,
    prepare_attempt,
    record_submission,
)
from bridge_store import bridge_root, write_bound_metadata


class SaveBridgeTurnProvenanceTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name).resolve()
        self.thread = "provenance-thread"
        self.prompt = "请核对附件。"
        self.answer = "完整回答。"
        self.bundle = self.repo / "source-bundle.zip"
        self.bundle.write_bytes(b"bundle bytes")
        self.digest = hashlib.sha256(self.bundle.read_bytes()).hexdigest()
        self.staged_name = "codex-bridge-provenance-thread-digest-source-bundle.zip"
        self.preflight = {
            "ready": True,
            "bridge_thread_id": self.thread,
            "browser_claim_verification": "verified",
            "turn_boundary_verification": "verified",
            "prompt_sha256": hashlib.sha256(self.prompt.encode()).hexdigest(),
            "pre_submit_boundary": "turn-before-send",
            "tab_owner_token": "owner-token",
            "expected_project_id": "",
            "expected_conversation_id": "conv",
            "observed_page_url": "https://chatgpt.com/c/conv",
            "attachment_verification": "verified",
            "attachment_name": self.staged_name,
            "attachment_sha256": self.digest,
            "staged_bundle": {
                "attachment_name": self.staged_name,
                "staged_sha256": self.digest,
                "source_sha256": self.digest,
            },
        }
        attempt = prepare_attempt(self.repo, self.thread, self.prompt, self.preflight)
        mark_send_started(self.repo, self.thread, attempt["attempt_id"])
        record_submission(
            self.repo,
            self.thread,
            attempt["attempt_id"],
            conversation_url="https://chatgpt.com/c/conv",
            owner="owner-token",
            prompt_sha256=self.preflight["prompt_sha256"],
            boundary="turn-before-send",
            remote_turn_id="turn-1",
            after_boundary="yes",
        )
        self.attempt_id = attempt["attempt_id"]
        codex_id = f"{self.thread}-codex"
        notes_dir = bridge_root(self.repo) / "codex-sessions" / codex_id
        notes_dir.mkdir(parents=True, exist_ok=True)
        notes = notes_dir / "notes.md"
        notes.write_text("notes\n", encoding="utf-8")
        session = notes_dir / "session.md"
        write_bound_metadata(
            session,
            {
                "codex_session_id": codex_id,
                "bridge_thread_id": self.thread,
                "bridge_project_id": "",
                "latest_snapshot": str(notes.relative_to(self.repo)),
            },
            ordered_keys=("codex_session_id", "bridge_thread_id", "bridge_project_id", "latest_snapshot"),
            immutable_keys=("codex_session_id", "bridge_thread_id", "bridge_project_id"),
        )

    def tearDown(self) -> None:
        self.temp.cleanup()

    def call(self, *, attachment_name: str = "") -> subprocess.CompletedProcess[str]:
        answer_file = self.repo / "answer.md"
        answer_file.write_text(self.answer, encoding="utf-8")
        return subprocess.run(
            [
                sys.executable,
                str(SCRIPT),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                self.thread,
                "--attempt-id",
                self.attempt_id,
                "--web-url",
                "https://chatgpt.com/c/conv",
                "--expected-conversation-id",
                "conv",
                "--remote-turn-id",
                "turn-1",
                "--bundle",
                str(self.bundle),
                "--prompt",
                self.prompt,
                "--answer-file",
                str(answer_file),
                "--capture-route",
                "native-read-thread",
                "--answer-format",
                "native-raw",
                "--response-completed-at",
                "2026-09-15T12:00:00+08:00",
                "--requested-model",
                "最新",
                "--selected-ui-label",
                "最新",
                "--model-selection-kind",
                "latest-alias",
                "--requested-thinking-intensity",
                "高",
                "--selected-thinking-intensity",
                "高",
                "--attachment-name",
                attachment_name,
                "--codex-session-id",
                f"{self.thread}-codex",
            ],
            capture_output=True,
            text=True,
            check=False,
        )

    def test_safe_staged_name_uses_checkpoint_provenance(self) -> None:
        result = self.call()
        self.assertEqual(result.returncode, 0, result.stderr)
        turn = Path(result.stdout.strip())
        text = turn.read_text(encoding="utf-8")
        self.assertIn(f"Attachment Name: {self.staged_name}", text)
        self.assertIn("Attachment Verification: verified", text)

    def test_checkpoint_name_drift_is_rejected(self) -> None:
        result = self.call(attachment_name="wrong-visible-name.zip")
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical staged name", result.stderr)

    def test_bundle_digest_drift_is_rejected(self) -> None:
        self.bundle.write_bytes(b"changed bundle bytes")
        result = self.call()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("canonical staged attachment digest", result.stderr)

    def test_source_digest_drift_is_rejected(self) -> None:
        attempt_path = (
            bridge_root(self.repo)
            / "attempts"
            / self.thread
            / f"{self.attempt_id}.json"
        )
        attempt = json.loads(attempt_path.read_text(encoding="utf-8"))
        attempt["preflight"]["staged_bundle"]["source_sha256"] = "b" * 64
        attempt_path.write_text(
            json.dumps(attempt, ensure_ascii=False, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        result = self.call()
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("source digest disagrees", result.stderr)


if __name__ == "__main__":
    unittest.main()
