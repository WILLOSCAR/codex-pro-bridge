from __future__ import annotations

import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import BridgeError, acquire_browser_lease, release_browser_lease
from browser_host import stage_browser_file, staging_windows_root
from devtools_upload import (
    DEVTOOLS_UPLOAD_ROUTE,
    NativeChooserRisk,
    build_upload_action_plan,
    native_chooser_cleanup_requirements,
    validate_upload_action_plan,
    validate_upload_receipt,
    verify_upload_result,
)
from model_controls import validate_model_control_trace


class DevToolsUploadTests(unittest.TestCase):
    def setUp(self) -> None:
        self.path = r"C:\BridgeStaging\bundle.zip"
        self.digest = "a" * 64
        self.plan = build_upload_action_plan(
            page_id="7",
            attachment_button_uid="uid-attach",
            menu_snapshot_uid="snapshot-2",
            menu_item_uid="uid-upload",
            windows_path=self.path,
            staged_sha256=self.digest,
        )

    def test_plan_requires_click_once_snapshot_then_direct_upload(self) -> None:
        normalized = validate_upload_action_plan(self.plan)
        self.assertEqual(normalized["route"], DEVTOOLS_UPLOAD_ROUTE)
        self.assertEqual(
            [action["type"] for action in normalized["actions"]],
            ["click", "fresh-snapshot", "upload_file"],
        )
        self.assertEqual(normalized["actions"][2]["snapshot_uid"], "snapshot-2")

    def test_clicking_menu_item_is_rejected(self) -> None:
        wrong = json.loads(json.dumps(self.plan))
        wrong["actions"][1] = {
            "type": "click",
            "target": "from-computer-menu-item",
            "uid": "uid-upload",
            "page_id": "7",
        }
        with self.assertRaisesRegex(BridgeError, "fresh upload-menu snapshot"):
            validate_upload_action_plan(wrong)

    def test_second_attachment_click_is_rejected(self) -> None:
        wrong = json.loads(json.dumps(self.plan))
        wrong["actions"][1]["type"] = "click"
        wrong["actions"][1]["target"] = "attachment-control"
        wrong["actions"][1]["uid"] = "uid-attach"
        wrong["actions"][1].pop("snapshot_uid")
        with self.assertRaisesRegex(BridgeError, "fresh upload-menu snapshot"):
            validate_upload_action_plan(wrong)

    def test_upload_must_use_fresh_snapshot_uid_and_verified_path(self) -> None:
        for field, value, expected in (
            ("snapshot_uid", "stale-snapshot", "fresh upload-menu snapshot UID"),
            ("windows_path", r"C:\other.zip", "verified staged path"),
        ):
            wrong = json.loads(json.dumps(self.plan))
            wrong["actions"][2][field] = value
            with self.subTest(field=field), self.assertRaisesRegex(BridgeError, expected):
                validate_upload_action_plan(wrong)

        stale = json.loads(json.dumps(self.plan))
        stale["actions"][1]["fresh"] = False
        with self.assertRaisesRegex(BridgeError, "must be fresh"):
            validate_upload_action_plan(stale)

    def test_failed_unknown_or_missing_chip_is_native_chooser_risk(self) -> None:
        for result in (
            {"status": "failed", "attachment_chip": False},
            {"status": "unknown", "attachment_chip": False},
            {"status": "accepted", "attachment_chip": False},
            {"status": "accepted", "attachment_chip": True, "isError": True},
        ):
            with self.subTest(result=result), self.assertRaises(NativeChooserRisk) as raised:
                verify_upload_result(self.plan, result)
            self.assertIn("Stop all further upload", str(raised.exception))
            self.assertEqual(raised.exception.cleanup["browser_ui"], "not-proven-clean; do not claim native chooser closure")

    def test_success_requires_chip_and_keeps_staged_cleanup_separate(self) -> None:
        result = verify_upload_result(
            self.plan,
            {
                "status": "accepted",
                "attachment_chip": True,
                "attachment_name": "bundle.zip",
            },
        )
        self.assertEqual(result["status"], "accepted")
        self.assertEqual(result["native_chooser"], "not-observed")
        self.assertEqual(result["staged_cleanup"], "separate-exact-file-cleanup-required")
        self.assertEqual(result["windows_path"], self.path)
        self.assertEqual(result["attachment_name"], "bundle.zip")
        self.assertTrue(result["upload_action_plan_sha256"])

    def test_success_receipt_binds_plan_page_path_digest_name_and_chip(self) -> None:
        receipt = verify_upload_result(
            self.plan,
            {
                "status": "accepted",
                "attachment_chip": True,
                "attachment_name": "bundle.zip",
            },
        )
        self.assertEqual(validate_upload_receipt(self.plan, receipt), receipt)
        for field, value, expected in (
            ("upload_action_plan_sha256", "b" * 64, "same action plan"),
            ("page_id", "8", "page_id"),
            ("windows_path", r"C:\\other.zip", "path"),
            ("staged_sha256", "c" * 64, "digest"),
            ("attachment_name", "other.zip", "attachment name"),
            ("attachment_chip", False, "attachment chip"),
            ("status", "blocked", "successful accepted"),
        ):
            wrong = dict(receipt)
            wrong[field] = value
            with self.subTest(field=field), self.assertRaisesRegex(BridgeError, expected):
                validate_upload_receipt(self.plan, wrong)

    def test_success_rejects_wrong_visible_attachment_name(self) -> None:
        with self.assertRaises(NativeChooserRisk):
            verify_upload_result(
                self.plan,
                {
                    "status": "accepted",
                    "attachment_chip": True,
                    "attachment_name": "other.zip",
                },
            )

    def test_cleanup_contract_has_no_global_dialog_killer(self) -> None:
        cleanup = native_chooser_cleanup_requirements()
        self.assertIn("manage_browser_staging.py cleanup", cleanup["staged_file"])
        self.assertIn("global Windows dialog killer", cleanup["safety"])

    def test_cli_emits_structured_native_chooser_block(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "validate_devtools_upload.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            plan_path = root / "plan.json"
            result_path = root / "result.json"
            plan_path.write_text(json.dumps(self.plan), encoding="utf-8")
            result_path.write_text(
                json.dumps({"status": "unknown", "attachment_chip": False}),
                encoding="utf-8",
            )
            completed = subprocess.run(
                [sys.executable, str(script), "--plan", str(plan_path), "--result", str(result_path)],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 2)
            output = json.loads(completed.stdout)
            self.assertEqual(output["status"], "blocked")
            self.assertTrue(output["native_chooser_risk"])
            self.assertEqual(output["browser_ui_cleanup"], "not-proven")
            self.assertEqual(output["upload_plan_identity"]["windows_path"], self.path)
            self.assertEqual(output["upload_plan_identity"]["staged_sha256"], self.digest)
            self.assertIn("staged_file", output["cleanup_requirements"])

    def test_cli_can_create_plan_under_expected_output_owner(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "validate_devtools_upload.py"
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory).resolve()
            owner = root / "preparation"
            owner.mkdir()
            plan_path = owner / "upload-action-plan.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--plan-output",
                    str(plan_path),
                    "--expected-output-dir",
                    str(owner),
                    "--page-id",
                    "7",
                    "--attachment-button-uid",
                    "uid-attach",
                    "--menu-snapshot-uid",
                    "snapshot-2",
                    "--menu-item-uid",
                    "uid-upload",
                    "--windows-path",
                    self.path,
                    "--staged-sha256",
                    self.digest,
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertEqual(output["status"], "plan-valid")
            self.assertEqual(Path(output["plan_file"]), plan_path)
            self.assertEqual(
                validate_upload_action_plan(json.loads(plan_path.read_text()))["route"],
                DEVTOOLS_UPLOAD_ROUTE,
            )
            raw_result_path = owner / "raw-upload-result.json"
            raw_result_path.write_text(
                json.dumps(
                    {
                        "status": "accepted",
                        "attachment_chip": True,
                        "attachment_name": "bundle.zip",
                    }
                ),
                encoding="utf-8",
            )
            receipt_path = owner / "upload-result.json"
            completed = subprocess.run(
                [
                    sys.executable,
                    str(script),
                    "--plan",
                    str(plan_path),
                    "--result",
                    str(raw_result_path),
                    "--receipt-output",
                    str(receipt_path),
                    "--expected-output-dir",
                    str(owner),
                ],
                capture_output=True,
                text=True,
                check=False,
            )
            self.assertEqual(completed.returncode, 0, completed.stderr)
            output = json.loads(completed.stdout)
            self.assertEqual(output["status"], "accepted")
            self.assertEqual(Path(output["result_file"]), receipt_path)
            self.assertEqual(json.loads(receipt_path.read_text())["status"], "accepted")

    def test_preflight_checks_devtools_plan_against_staged_path_and_page(self) -> None:
        thread_id = "devtools-plan-preflight"
        conversation_id = "devtools-plan-conversation"
        url = f"https://chatgpt.com/c/{conversation_id}"
        env_keys = (
            "CODEX_PRO_BRIDGE_BROWSER_STATE_DIR",
            "CODEX_BRIDGE_STAGING_WSL_ROOT",
            "CODEX_BRIDGE_STAGING_WINDOWS_ROOT",
            "CODEX_BRIDGE_STAGING_LOCK",
        )
        previous_env = {key: os.environ.get(key) for key in env_keys}
        try:
            with tempfile.TemporaryDirectory() as directory:
                root = Path(directory).resolve()
                repo = root / "repo"
                repo.mkdir()
                state = root / "browser-state"
                stage_root = root / "staging"
                stage_root.mkdir()
                os.environ["CODEX_PRO_BRIDGE_BROWSER_STATE_DIR"] = str(state)
                os.environ["CODEX_BRIDGE_STAGING_WSL_ROOT"] = str(stage_root)
                os.environ["CODEX_BRIDGE_STAGING_WINDOWS_ROOT"] = r"C:\BridgeStaging"
                os.environ["CODEX_BRIDGE_STAGING_LOCK"] = str(root / "staging.lock")
                source = repo / "bundle.zip"
                source.write_bytes(b"verified bundle")
                staged = stage_browser_file(source, thread_id=thread_id)
                plan = build_upload_action_plan(
                    page_id="7",
                    attachment_button_uid="uid-attach",
                    menu_snapshot_uid="snapshot-2",
                    menu_item_uid="uid-upload",
                    windows_path=staged["staged_windows_path"],
                    staged_sha256=staged["source_sha256"],
                )
                plan_path = repo / "upload-action-plan.json"
                plan_path.write_text(json.dumps(plan), encoding="utf-8")
                result_path = repo / "upload-result.json"
                result_path.write_text(
                    json.dumps(
                        verify_upload_result(
                            plan,
                            {
                                "status": "accepted",
                                "attachment_chip": True,
                                "attachment_name": staged["attachment_name"],
                            },
                        )
                    ),
                    encoding="utf-8",
                )
                model_receipt_path = repo / "model-controls.json"
                model_receipt_path.write_text(
                    json.dumps(
                        validate_model_control_trace(
                            {
                                "schema_version": "model-controls/v1",
                                "page_id": "7",
                                "tab_owner_token": "placeholder",
                                "observed_page_url": url,
                                "requested_model": "最新",
                                "selected_model": "最新",
                                "model_selection_kind": "latest-alias",
                                "requested_thinking_intensity": "高",
                                "selected_thinking_intensity": "高",
                                "initial_model": "最新",
                                "initial_thinking_intensity": "高",
                                "counts": {
                                    "combined_initial_read": 1,
                                    "model_menu_open": 0,
                                    "model_selection": 0,
                                    "thinking_control_open": 0,
                                    "thinking_adjustment": 0,
                                    "thinking_progress_read": 0,
                                    "combined_final_confirmation": 1,
                                    "post_preflight_recheck": 0,
                                },
                            }
                        )
                    ),
                    encoding="utf-8",
                )
                claim = acquire_browser_lease(
                    repo,
                    holder="devtools-plan-worker",
                    thread_id=thread_id,
                    expected_conversation_id=conversation_id,
                    scope="conversation",
                )
                try:
                    model_receipt = json.loads(model_receipt_path.read_text())
                    model_receipt["tab_owner_token"] = claim["tab_owner_token"]
                    model_receipt_path.write_text(json.dumps(model_receipt), encoding="utf-8")
                    preflight = Path(__file__).resolve().parents[1] / "scripts" / "check_browser_preflight.py"
                    base_args = [
                        sys.executable,
                        str(preflight),
                        "--repo",
                        str(repo),
                        "--bridge-thread-id",
                        thread_id,
                        "--browser-lease-token",
                        claim["token"],
                        "--browser-profile",
                        claim["browser_profile"],
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
                        "--source-bundle",
                        str(source),
                        "--bundle",
                        staged["staged_wsl_path"],
                        "--attachment-name",
                        staged["attachment_name"],
                        "--upload-control",
                        "devtools-direct-menu-upload",
                        "--mcp-host-os",
                        "windows",
                        "--browser-host-os",
                        "windows",
                        "--mcp-temp-root",
                        str(staging_windows_root()),
                        "--observed-conversation-id",
                        conversation_id,
                        "--expected-conversation-id",
                        conversation_id,
                        "--observed-page-url",
                        url,
                        "--matching-page-count",
                        "1",
                        "--pages-json",
                        json.dumps([{"page_id": "7", "url": url}]),
                        "--owners-json",
                        json.dumps([{"page_id": "7", "owner_token": claim["tab_owner_token"]}]),
                        "--observed-page-id",
                        "7",
                        "--snapshot-page-id",
                        "7",
                        "--observed-tab-owner-token",
                        claim["tab_owner_token"],
                        "--pre-submit-boundary",
                        "turn-before",
                        "--prompt-sha256",
                        hashlib.sha256(b"prompt").hexdigest(),
                    ]

                    def run_preflight(
                        *,
                        include_plan: bool,
                        include_result: bool,
                        include_model_receipt: bool = True,
                    ) -> subprocess.CompletedProcess[str]:
                        args = list(base_args)
                        if include_plan:
                            args.extend(("--upload-action-plan", str(plan_path)))
                        if include_result:
                            args.extend(("--upload-result", str(result_path)))
                        if include_model_receipt:
                            args.extend(("--model-control-receipt", str(model_receipt_path)))
                        return subprocess.run(
                            args,
                        capture_output=True,
                        text=True,
                        check=False,
                        env=os.environ.copy(),
                        )

                    missing_plan = run_preflight(include_plan=False, include_result=False)
                    self.assertNotEqual(missing_plan.returncode, 0)
                    missing_receipt = run_preflight(include_plan=True, include_result=False)
                    self.assertNotEqual(missing_receipt.returncode, 0)
                    missing_controls = run_preflight(
                        include_plan=True,
                        include_result=True,
                        include_model_receipt=False,
                    )
                    self.assertNotEqual(missing_controls.returncode, 0)
                    completed = run_preflight(include_plan=True, include_result=True)
                    self.assertEqual(completed.returncode, 0, completed.stderr)
                    output = json.loads(completed.stdout)
                    self.assertEqual(output["upload_action_plan_verification"], "verified")
                    self.assertEqual(output["upload_action_plan"], str(plan_path))
                    self.assertEqual(output["upload_result_verification"], "verified")
                    self.assertEqual(output["upload_result"], str(result_path))
                    self.assertEqual(output["model_control_receipt_verification"], "verified")
                    connector_args = list(base_args)
                    connector_args[connector_args.index("devtools-direct-menu-upload")] = (
                        "codex-chrome-visible-menu"
                    )
                    connector = subprocess.run(
                        connector_args,
                        capture_output=True,
                        text=True,
                        check=False,
                        env=os.environ.copy(),
                    )
                    self.assertEqual(connector.returncode, 0, connector.stderr)
                    connector_output = json.loads(connector.stdout)
                    self.assertEqual(connector_output["upload_action_plan_verification"], "not-provided")
                finally:
                    release_browser_lease(repo, token=claim["token"])
        finally:
            for key, value in previous_env.items():
                if value is None:
                    os.environ.pop(key, None)
                else:
                    os.environ[key] = value

    def test_authoritative_contracts_distinguish_devtools_and_connector_routes(self) -> None:
        skill_root = Path(__file__).resolve().parents[1]
        browser = (skill_root / "references" / "browser_adapters.md").read_text()
        protocol = (skill_root / "references" / "bridge_protocol.md").read_text()
        handoff = (skill_root / "references" / "executor_handoff.md").read_text()
        question_window = (skill_root / "SKILL.md").read_text()
        agent = (
            skill_root.parents[2] / "agents" / "bridge-executor.toml.example"
        ).read_text()
        for text in (browser, protocol, handoff, question_window, agent):
            self.assertIn("upload_file", text)
            self.assertIn("Upload from computer", text)
            self.assertIn("native chooser", text.lower())
        self.assertIn("waitForEvent(\"filechooser\")", browser)
        self.assertIn("valid for DevTools", handoff)
        self.assertIn("global Windows dialog killer", agent)


if __name__ == "__main__":
    unittest.main()
