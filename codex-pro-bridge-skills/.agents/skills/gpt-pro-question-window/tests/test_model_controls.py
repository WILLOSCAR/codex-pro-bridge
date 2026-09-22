from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import BridgeError
from model_controls import (
    assess_model_selection,
    validate_model_control_trace,
)


class ModelControlsTest(unittest.TestCase):
    def trace(self, **changes):
        value = {
            "schema_version": "model-controls/v1",
            "page_id": "7",
            "tab_owner_token": "owner-a",
            "observed_page_url": "https://chatgpt.com/g/g-p-demo/project",
            "requested_model": "最新",
            "selected_model": "最新",
            "model_selection_kind": "latest-alias",
            "requested_thinking_intensity": "极高",
            "selected_thinking_intensity": "极高",
            "initial_model": "最新",
            "initial_thinking_intensity": "即时",
            "counts": {
                "combined_initial_read": 1,
                "model_menu_open": 0,
                "model_selection": 0,
                "thinking_control_open": 1,
                "thinking_adjustment": 2,
                "thinking_progress_read": 2,
                "combined_final_confirmation": 1,
                "post_preflight_recheck": 0,
            },
        }
        for key, item in changes.items():
            if key.startswith("count_"):
                value["counts"][key.removeprefix("count_")] = item
            else:
                value[key] = item
        return value

    def test_exact_named_model_is_verified(self) -> None:
        self.assertEqual(
            assess_model_selection("GPT-5.6 Sol", "GPT-5.6 Sol", "exact"),
            "verified",
        )

    def test_latest_alias_is_not_reported_as_exact_model(self) -> None:
        self.assertEqual(
            assess_model_selection("最新", "最新", "latest-alias"),
            "alias-selected",
        )

    def test_mismatch_and_missing_observation(self) -> None:
        self.assertEqual(
            assess_model_selection("GPT-6 Astra", "最新", "exact"), "mismatch"
        )
        self.assertEqual(assess_model_selection("", "最新", "latest-alias"), "unverified")

    def test_bounded_trace_accepts_one_initial_and_one_final_pair(self) -> None:
        result = validate_model_control_trace(self.trace())
        self.assertEqual(result["status"], "verified")
        self.assertEqual(result["counts"]["model_menu_open"], 0)

    def test_matching_controls_forbid_menu_or_adjustment(self) -> None:
        trace = self.trace(
            initial_thinking_intensity="极高",
            count_thinking_control_open=0,
            count_thinking_adjustment=0,
            count_thinking_progress_read=0,
        )
        self.assertEqual(validate_model_control_trace(trace)["status"], "verified")
        trace["counts"]["model_menu_open"] = 1
        with self.assertRaisesRegex(BridgeError, "model menu open count"):
            validate_model_control_trace(trace)

    def test_third_pair_check_and_post_preflight_recheck_are_rejected(self) -> None:
        with self.assertRaisesRegex(BridgeError, "exactly one combined final"):
            validate_model_control_trace(self.trace(count_combined_final_confirmation=2))
        with self.assertRaisesRegex(BridgeError, "must not be rechecked"):
            validate_model_control_trace(self.trace(count_post_preflight_recheck=1))

    def test_progress_reads_must_match_bounded_thinking_steps(self) -> None:
        with self.assertRaisesRegex(BridgeError, "each thinking adjustment"):
            validate_model_control_trace(self.trace(count_thinking_progress_read=3))
        with self.assertRaisesRegex(BridgeError, "between 1 and 6"):
            validate_model_control_trace(self.trace(count_thinking_adjustment=7, count_thinking_progress_read=7))

    def test_cli_persists_receipt_without_overwrite(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "validate_model_controls.py"
        with tempfile.TemporaryDirectory() as directory:
            owner = Path(directory).resolve()
            trace = owner / "trace.json"
            receipt = owner / "receipt.json"
            trace.write_text(json.dumps(self.trace()), encoding="utf-8")
            args = [
                sys.executable,
                str(script),
                "--trace",
                str(trace),
                "--receipt-output",
                str(receipt),
                "--expected-output-dir",
                str(owner),
            ]
            first = subprocess.run(args, capture_output=True, text=True, check=False)
            self.assertEqual(first.returncode, 0, first.stderr)
            self.assertEqual(json.loads(receipt.read_text())["status"], "verified")
            second = subprocess.run(args, capture_output=True, text=True, check=False)
            self.assertNotEqual(second.returncode, 0)
            self.assertIn("refusing to overwrite", second.stderr)


if __name__ == "__main__":
    unittest.main()
