from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

SHARED = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED))

from bridge_store import BridgeError
from executor_handoff import validate_handoff


TEST_ROOT = Path(tempfile.gettempdir()).resolve() / "codex-bridge-handoff-tests"
TEST_REPO = TEST_ROOT / "repo"


def base(**changes):
    value = {
        "schema_version": "executor_handoff/v2",
        "mode": "prepare-and-run",
        "repo": str(TEST_REPO),
        "bridge_thread_id": "bridge-thread",
        "request_file": str(TEST_REPO / "request.json"),
        "request_sha256": "a" * 64,
        "allow_send": True,
        "allowed_external_actions": ["send-once", "capture-reply"],
        "expected_output_dir": str(TEST_REPO / "output"),
        "requested_model": "最新",
        "model_selection_kind": "latest-alias",
        "requested_thinking_intensity": "高",
        "context_policy": "none",
        "max_files": 0,
        "attachment_policy": "none",
        "target_project_url": "",
        "binding_action": "none",
        "business_deadline": None,
    }
    value.update(changes)
    return value


class ExecutorHandoffTests(unittest.TestCase):
    def test_v2_requires_model_controls_for_new_round(self):
        for field in ("requested_model", "model_selection_kind", "requested_thinking_intensity"):
            handoff = base()
            handoff.pop(field)
            with self.subTest(field=field), self.assertRaises(BridgeError):
                validate_handoff(handoff, require_new_round=True)

    def test_v1_prepare_is_rejected(self):
        with self.assertRaisesRegex(BridgeError, "v1 is recover-only"):
            validate_handoff(base(schema_version="executor_handoff/v1"), require_new_round=True)

    def test_v1_recover_only_remains_compatible(self):
        handoff = base(
            schema_version="executor_handoff/v1",
            mode="recover-only",
            allow_send=False,
            attempt_id="attempt-1",
            allowed_external_actions=["read-exact-turn", "legacy-cleanup"],
        )
        # v1 recoveries do not need new-round model or Project controls.
        for field in (
            "requested_model",
            "model_selection_kind",
            "requested_thinking_intensity",
            "target_project_url",
            "binding_action",
        ):
            handoff.pop(field, None)
        self.assertEqual(validate_handoff(handoff)["attempt_id"], "attempt-1")

    def test_v2_recover_only_cannot_authorize_send(self):
        handoff = base(mode="recover-only", allow_send=True, attempt_id="attempt-1")
        with self.assertRaisesRegex(BridgeError, "recover-only handoff"):
            validate_handoff(handoff)

    def test_prepare_and_run_cannot_carry_attempt_identity(self):
        with self.assertRaisesRegex(BridgeError, "must not include attempt_id"):
            validate_handoff(base(attempt_id="attempt-1"), require_new_round=True)

    def test_handoff_paths_stay_inside_repo(self):
        with self.assertRaisesRegex(BridgeError, "request_file must stay"):
            validate_handoff(base(request_file=str(TEST_ROOT / "outside-request.json")))
        with self.assertRaisesRegex(BridgeError, "expected_output_dir must stay"):
            validate_handoff(base(expected_output_dir=str(TEST_ROOT / "outside-output")))

    def test_project_identity_is_all_or_nothing(self):
        with self.assertRaisesRegex(BridgeError, "both bridge_project_id"):
            validate_handoff(base(bridge_project_id="local"), require_new_round=True)
        with self.assertRaisesRegex(BridgeError, "Standalone handoff"):
            validate_handoff(base(target_project_url="https://chatgpt.com/g/g-p-id/project"), require_new_round=True)

    def test_none_context_cannot_authorize_upload(self):
        with self.assertRaisesRegex(BridgeError, "cannot authorize upload-task-bundle"):
            validate_handoff(
                base(allowed_external_actions=["upload-task-bundle", "send-once", "capture-reply"]),
                require_new_round=True,
            )

    def test_repository_context_requires_bundle_authorization(self):
        handoff = base(
            context_policy="explicit",
            max_files=1,
            attachment_policy="bundle",
        )
        with self.assertRaisesRegex(BridgeError, "requires upload-task-bundle"):
            validate_handoff(handoff, require_new_round=True)

    def test_context_and_attachment_policies_must_agree(self):
        handoff = base(
            context_policy="none",
            max_files=0,
            attachment_policy="bundle",
            allowed_external_actions=["send-once", "capture-reply"],
        )
        with self.assertRaisesRegex(BridgeError, "requires attachment_policy none"):
            validate_handoff(handoff, require_new_round=True)

    def test_external_actions_are_known_and_unique(self):
        with self.assertRaisesRegex(BridgeError, "unknown actions"):
            validate_handoff(
                base(allowed_external_actions=["send-once", "capture-reply", "delete"]),
                require_new_round=True,
            )
        with self.assertRaisesRegex(BridgeError, "must not contain duplicates"):
            validate_handoff(
                base(allowed_external_actions=["send-once", "send-once", "capture-reply"]),
                require_new_round=True,
            )


if __name__ == "__main__":
    unittest.main()
