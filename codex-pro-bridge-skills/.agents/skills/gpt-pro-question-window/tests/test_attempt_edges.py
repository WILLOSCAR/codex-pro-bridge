from __future__ import annotations

import datetime as dt
import sys
import tempfile
import unittest
from pathlib import Path


SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_attempts import (  # noqa: E402
    complete_attempt,
    digest,
    mark_send_started,
    prepare_attempt,
    record_submission,
)
from bridge_store import BridgeError, append_event, file_sha256  # noqa: E402


class AttemptEdgeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name).resolve() / "repo"
        self.repo.mkdir()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _submitted_attempt(self) -> dict:
        prompt = "pinned prompt"
        preflight = {
            "ready": True,
            "bridge_thread_id": "attempt-thread",
            "prompt_sha256": digest(prompt),
            "pre_submit_boundary": "turn-before-send",
            "tab_owner_token": "pinned-owner",
            "expected_project_id": "",
            "expected_conversation_id": "pinned-conversation",
            "conversation_bootstrap": False,
            "observed_page_url": "https://chatgpt.com/c/pinned-conversation",
            "browser_claim_verification": "verified",
            "turn_boundary_verification": "verified",
        }
        deadline = (
            dt.datetime.now().astimezone() + dt.timedelta(minutes=10)
        ).isoformat(timespec="seconds")
        attempt = prepare_attempt(
            self.repo, "attempt-thread", prompt, preflight, deadline
        )
        mark_send_started(self.repo, "attempt-thread", attempt["attempt_id"])
        return record_submission(
            self.repo,
            "attempt-thread",
            attempt["attempt_id"],
            conversation_url="https://chatgpt.com/c/pinned-conversation",
            owner="pinned-owner",
            prompt_sha256=digest(prompt),
            boundary="turn-before-send",
            remote_turn_id="pinned-remote-turn",
            after_boundary="yes",
        )

    def test_completion_rejects_exchange_for_another_remote_turn(self) -> None:
        attempt = self._submitted_attempt()
        wrong_turn = self.repo / "wrong-turn.md"
        wrong_turn.write_text("answer from another remote turn\n", encoding="utf-8")
        append_event(
            self.repo,
            thread_id="attempt-thread",
            event_type="gpt-exchange",
            actor="gpt-pro",
            artifact={
                "kind": "gpt-pro-turn",
                "path": "wrong-turn.md",
                "sha256": file_sha256(wrong_turn),
            },
            data={
                "remote_turn_id": "different-remote-turn",
                "observed_conversation_id": "pinned-conversation",
            },
        )

        with self.assertRaisesRegex(BridgeError, "pinned attempt"):
            complete_attempt(
                self.repo, "attempt-thread", attempt["attempt_id"], wrong_turn
            )


if __name__ == "__main__":
    unittest.main()
