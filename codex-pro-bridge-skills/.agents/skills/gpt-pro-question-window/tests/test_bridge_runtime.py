"""Offline acceptance: canonical gates/ledger with a deterministic browser double."""
from __future__ import annotations

import concurrent.futures
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import tempfile
import time
import unittest
from unittest.mock import patch

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(SCRIPTS.parents[1] / ".shared"))
from bridge_store import BridgeError, file_sha256, now_iso
from bridge_attempts import active_attempt, digest
from bridge_runtime import pipeline
from bridge_runtime.browser import Browser, unique_uid
from bridge_runtime.jobs import Jobs, Job, write_json, worker_lock
from bridge_mcp import serve


class RuntimeTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.cleanup_temp)
        self.root = Path(self.temp.name).resolve()
        self.repo = self.root / "repo"
        self.repo.mkdir()
        self.env = patch.dict(os.environ, {
            "CODEX_PRO_BRIDGE_BROWSER_STATE_DIR": str(self.root / "browser-state"),
            "CODEX_BRIDGE_STAGING_WSL_ROOT": str(self.root / "staging"),
            "CODEX_BRIDGE_STAGING_WINDOWS_ROOT": r"C:\BridgeTest",
            "CODEX_PRO_BRIDGE_HOST_CONFIG": "",
            "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"})
        self.env.start()
        self.addCleanup(self.env.stop)
        notes = self.repo / "notes.md"
        notes.write_text("frozen evidence", encoding="utf-8")
        request = {"repo": str(self.repo), "bridge_thread_id": "runtime-test", "goal": "Test",
            "question": "请原样回答 OK", "notes": "notes.md", "files": [],
            "context_policy": "none", "max_files": 0, "file_digests": {"notes.md": file_sha256(notes)}}
        request_path = self.repo / "request.json"
        write_json(request_path, request)
        self.h = {"schema_version": "executor_handoff/v2", "mode": "prepare-and-run", "repo": str(self.repo),
            "bridge_thread_id": "runtime-test", "request_file": str(request_path),
            "request_sha256": file_sha256(request_path), "allow_send": True,
            "allowed_external_actions": ["send-once", "capture-reply"],
            "expected_output_dir": str(self.repo / "artifacts"), "requested_model": "最新",
            "model_selection_kind": "latest-alias", "requested_thinking_intensity": "极高",
            "context_policy": "none", "max_files": 0, "attachment_policy": "none",
            "target_project_url": "", "binding_action": "none", "business_deadline": None}
        self.h_path = self.repo / "handoff.json"
        write_json(self.h_path, self.h)
        self.config = {"state_dir": str(self.root / "jobs"), "allowed_repos": [str(self.repo)],
            "browser_command": [sys.executable, "-c", "pass"], "ui": {
                "model": "#model", "thinking": "#thinking", "composer": "#composer",
                "attachment_chip": "#composer .chip", "model_menu_labels": ["Model"],
                "thinking_menu_labels": ["Thinking"], "attachment_labels": ["Add files"],
                "upload_labels": ["Upload from computer"], "send_labels": ["Send"]}}
        self.config_path = self.root / "runtime.json"
        write_json(self.config_path, self.config)
        self.jobs = Jobs(self.config_path)

    def submit(self):
        with patch.object(Jobs, "_spawn"):
            result = self.jobs.submit(str(self.h_path), file_sha256(self.h_path))
        return Job(self.jobs.directory(result["job_id"]))

    def cleanup_temp(self):
        # Job state can be persisted just before the detached process releases
        # its stdout log. Windows correctly refuses unlinking that open handle.
        deadline = time.monotonic() + 5
        while True:
            try:
                self.temp.cleanup()
                return
            except PermissionError:
                if os.name != "nt" or time.monotonic() >= deadline:
                    raise
                time.sleep(0.05)

    def test_concurrent_submit_is_idempotent(self):
        with patch.object(Jobs, "_spawn") as spawn, concurrent.futures.ThreadPoolExecutor(4) as executor:
            results = list(executor.map(lambda _: self.jobs.submit(str(self.h_path), file_sha256(self.h_path)), range(8)))
        self.assertEqual(len({r["job_id"] for r in results}), 1)
        self.assertEqual(spawn.call_count, 1)

    def test_frozen_inputs_and_repository_allowlist(self):
        (self.repo / "notes.md").write_text("changed", encoding="utf-8")
        with self.assertRaisesRegex(ValueError, "drift"):
            self.submit()
        (self.repo / "notes.md").write_text("frozen evidence", encoding="utf-8")
        self.jobs.config["allowed_repos"] = [str(self.root / "other")]
        with self.assertRaisesRegex(BridgeError, "allowed_repos"):
            self.submit()

    def test_malformed_job_ids_cannot_escape_store(self):
        for identifier in ("../state", "a" * 63, "z" * 64):
            with self.subTest(identifier=identifier), self.assertRaises(BridgeError):
                self.jobs.status(identifier)

    def test_wait_timeout_preserves_job_and_does_not_spawn(self):
        job = self.submit()
        with patch.object(Jobs, "_spawn") as spawn:
            result = self.jobs.wait(job.data["job_id"], 0.01)
        self.assertEqual(result["state"], "queued")
        self.assertFalse(result["may_resend"])
        spawn.assert_not_called()

    def test_cleanup_recovers_missing_receipt_without_redeleting(self):
        job = self.submit()
        job.update(preparation={"staging": {"staged_execution_path": str(self.root / "already-removed.zip"),
                                            "source_sha256": "a" * 64}})
        with patch.object(pipeline, "cleanup_staged_file") as remove:
            pipeline.cleanup(job)
        remove.assert_not_called()
        self.assertEqual(job.data["staging_cleaned"]["status"], "already-absent")
        self.assertFalse(job.data["staging_cleaned"]["cleaned"])

    def test_single_worker_lock(self):
        job = self.submit()
        with worker_lock(job.directory) as first:
            self.assertTrue(first)
            with worker_lock(job.directory) as second:
                self.assertFalse(second)

    def test_pre_send_resume_never_repeats_upload(self):
        job = self.submit()
        job.update(stage="upload-started")
        with patch.object(pipeline, "Client") as client, self.assertRaisesRegex(BridgeError, "will not be replayed"):
            pipeline.execute(job, self.config)
        client.assert_not_called()

    def test_unverified_source_inventory_blocks_before_browser(self):
        from unittest.mock import Mock
        request_path = Path(self.h["request_file"])
        request = json.loads(request_path.read_text(encoding="utf-8"))
        request["bridge_project_id"] = "project-test"
        write_json(request_path, request)
        self.h.update(request_sha256=file_sha256(request_path), bridge_project_id="project-test",
                      remote_project_id="g-p-demo", binding_action="reuse",
                      target_project_url="https://chatgpt.com/g/g-p-demo/project")
        write_json(self.h_path, self.h)
        self.config["ui"].update(workspace="#workspace", account="#account")
        write_json(self.config_path, self.config)
        self.jobs = Jobs(self.config_path)
        job = self.submit()
        store = Mock()
        store.load_binding.return_value = {"status": "active", "remote_project_id": "g-p-demo"}
        store.verify.return_value = {"project_status": "active", "inventory_verified": False, "unsynced_source_count": 0}
        store.project_for_thread.return_value = "project-test"
        with patch.object(pipeline, "BridgeProjectStore", return_value=store), patch.object(pipeline, "Client") as client:
            with self.assertRaisesRegex(BridgeError, "reconciliation"):
                pipeline.execute(job, self.config)
        client.assert_not_called()

    def test_mcp_dispatch_and_bad_arguments(self):
        source = io.StringIO("\n".join(json.dumps(v) for v in [
            {"jsonrpc": "2.0", "id": 1, "method": "initialize"},
            {"jsonrpc": "2.0", "id": 2, "method": "tools/list"},
            {"jsonrpc": "2.0", "id": 3, "method": "tools/call", "params": {
                "name": "bridge_status", "arguments": {"job_id": "../outside"}}}]) + "\n")
        sink = io.StringIO()
        serve(self.jobs, source, sink)
        messages = {m["id"]: m for m in map(json.loads, sink.getvalue().splitlines())}
        self.assertEqual(len(messages[2]["result"]["tools"]), 5)
        self.assertTrue(messages[3]["result"]["isError"])

    def test_unique_snapshot_uid_rejects_duplicate_and_disabled(self):
        self.assertEqual(unique_uid('uid=2_1 button "Send"', ("button",), ["Send"]), "2_1")
        for text in ('uid=2_1 button "Send" [disabled]', 'uid=2_1 button "Send"\nuid=2_2 button "Send"'):
            with self.assertRaises(BridgeError):
                unique_uid(text, ("button",), ["Send"])

    def test_submission_requires_exact_prompt_after_boundary(self):
        attempt = {"tab_owner_token": "owner", "project_id": "", "conversation_id": "chat",
                   "pre_submit_boundary": "before", "prompt": "frozen prompt"}
        observation = {"url": "https://chatgpt.com/c/chat", "owner": "owner", "messages": [
            {"id": "before", "role": "assistant", "text": ""},
            {"id": "user", "role": "user", "text": "frozen prompt"}]}
        self.assertEqual(pipeline.observed_submission(observation, attempt), "user")
        observation["messages"][1]["text"] = "unrelated"
        with self.assertRaisesRegex(BridgeError, "no resend"):
            pipeline.observed_submission(observation, attempt)

    def test_upload_uses_one_menu_click_and_one_upload(self):
        from unittest.mock import Mock
        client = Mock()
        browser = Browser(client, {"attachment_labels": ["Add files"], "upload_labels": ["Upload from computer"],
                                   "attachment_chip": "#composer .chip"})
        browser.page_id, browser.owner, browser.url = "7", "owner", "https://chatgpt.com/"
        browser.assert_identity = Mock()
        browser.snapshot = Mock(side_effect=['uid=7_1 button "Add files"', 'uid=7_2 menuitem "Upload from computer"'])
        browser.evaluate = Mock(return_value=[{"name": "bundle.zip", "busy": False}])
        marker = Mock()
        receipt = browser.upload({"staged_browser_path": r"C:\BridgeTest\bundle.zip", "source_sha256": "a" * 64,
                                 "attachment_name": "bundle.zip"}, self.root / "plan.json", self.root / "receipt.json",
                                 write_json, marker)
        self.assertEqual([call.args[0] for call in client.call.call_args_list], ["click", "upload_file"])
        marker.assert_called_once()
        self.assertEqual(receipt["status"], "accepted")

    def test_upload_error_never_retries(self):
        from unittest.mock import Mock
        client = Mock()
        client.call.side_effect = [{}, RuntimeError("unknown upload outcome")]
        browser = Browser(client, {"attachment_labels": ["Add files"], "upload_labels": ["Upload from computer"]})
        browser.page_id = "7"
        browser.assert_identity = Mock()
        browser.snapshot = Mock(side_effect=['uid=7_1 button "Add files"', 'uid=7_2 menuitem "Upload from computer"'])
        with self.assertRaisesRegex(RuntimeError, "unknown upload"):
            browser.upload({"staged_browser_path": r"C:\BridgeTest\bundle.zip", "source_sha256": "a" * 64,
                            "attachment_name": "bundle.zip"}, self.root / "plan.json", self.root / "receipt.json",
                            write_json, Mock())
        self.assertEqual(client.call.call_count, 2)
        self.assertFalse((self.root / "receipt.json").exists())

    def test_detached_worker_outlives_mcp_connection(self):
        fake = self.root / "slow-browser.py"
        fake.write_text('''import json, sys, time
for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg: continue
    if msg["method"] == "initialize":
        time.sleep(2)
        result = {"protocolVersion": "2024-11-05", "capabilities": {}, "serverInfo": {"name": "test", "version": "1"}}
    else: result = {"tools": []}
    print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": result}), flush=True)
''', encoding="utf-8")
        self.config["browser_command"] = [sys.executable, str(fake)]
        write_json(self.config_path, self.config)
        request = {"jsonrpc": "2.0", "id": 1, "method": "tools/call", "params": {
            "name": "bridge_submit", "arguments": {"handoff_path": str(self.h_path),
                                                     "handoff_sha256": file_sha256(self.h_path)}}}
        parent = subprocess.run([sys.executable, str(SCRIPTS / "bridge_mcp.py"), "--config", str(self.config_path)],
            input=json.dumps(request) + "\n", capture_output=True, text=True, encoding="utf-8", timeout=40, check=True)
        job_id = json.loads(parent.stdout)["result"]["structuredContent"]["job_id"]
        deadline = time.monotonic() + 60
        while time.monotonic() < deadline:
            result = self.jobs.status(job_id)
            if result["state"] == "blocked" and not result["worker_recently_alive"]:
                break
            time.sleep(0.1)
        self.assertEqual(result["state"], "blocked")
        self.assertIn("lacks list_pages", result["error"])
        self.assertTrue((self.jobs.directory(job_id) / "job.json").is_file())

    def browser_double(self, crash_after_send=False):
        state = {"url": "https://chatgpt.com/", "owner": "", "sent": 0, "prompt": "", "messages": []}
        class Client:
            def __init__(self, *args, **kwargs):
                pass
            def close(self):
                pass
            def call(self, name, **kwargs):
                if name == "click":
                    if crash_after_send == "before":
                        raise RuntimeError("injected disconnect before click reached browser")
                    state["sent"] += 1
                    state["url"] = "https://chatgpt.com/c/runtime-chat"
                    state["messages"] = [{"id": "runtime-user", "role": "user", "text": state["prompt"]}]
                    if crash_after_send and state["sent"] == 1:
                        raise RuntimeError("injected disconnect after accepted Send")
                return {}
        class Browser:
            def __init__(self, client, ui):
                self.client = client
                self.page_id, self.owner, self.url = None, "", ""
            def pages(self):
                return [{"page_id": "7", "url": state["url"]}]
            def owners(self, pages):
                return [{"page_id": "7", "owner_token": state["owner"]}]
            def evaluate(self, function, page_id=None):
                if "sessionStorage.setItem" in function:
                    state["owner"] = json.loads(re.search(r'sessionStorage.setItem\(key, ("[^"]+")\)', function)[1])
                    return True
                if "waitForReply" in function:
                    return {"status": "ready-for-capture", "assistant_turn_id": "runtime-answer", "observed_at": now_iso()}
                raise AssertionError(function)
            def assert_identity(self):
                if self.owner != state["owner"] or self.url != state["url"]:
                    raise BridgeError("fake owner mismatch")
            def snapshot(self):
                self.assert_identity()
                return 'uid=7_1 button "Send"'
            def composer_text(self):
                return state["prompt"].strip() if not state["sent"] else ""
            def attachment_count(self):
                return 0
            def adjust_controls(self, h):
                from model_controls import validate_model_control_trace
                return validate_model_control_trace({"schema_version": "model-controls/v1", "page_id": "7",
                    "tab_owner_token": self.owner, "observed_page_url": self.url,
                    "requested_model": "最新", "selected_model": "最新", "model_selection_kind": "latest-alias",
                    "requested_thinking_intensity": "极高", "selected_thinking_intensity": "极高",
                    "initial_model": "最新", "initial_thinking_intensity": "极高", "counts": {
                        "combined_initial_read": 1, "model_menu_open": 0, "model_selection": 0,
                        "thinking_control_open": 0, "thinking_adjustment": 0, "thinking_progress_read": 0,
                        "combined_final_confirmation": 1, "post_preflight_recheck": 0}})
            def fill_prompt(self, prompt):
                state["prompt"] = prompt
            def messages(self, **kwargs):
                return {"url": state["url"], "owner": state["owner"], "messages": state["messages"]}
        def capture(browser, attempt, output, config):
            output.write_text("# 完整原始回答\n\nOK\n", encoding="utf-8")
            return {"answer_path": str(output), "answer_sha256": file_sha256(output),
                    "assistant_turn_id": "runtime-answer", "completed_at": now_iso()}
        return state, Client, Browser, capture

    def test_connection_only_interruption_reuses_frozen_preparation(self):
        job = self.submit()
        with patch.object(pipeline, "Client", side_effect=RuntimeError("connection pending")):
            with self.assertRaisesRegex(RuntimeError, "connection pending"):
                pipeline.execute(job, self.config)
        frozen = dict(job.data["preparation"])
        self.assertEqual(job.data["browser_phase"], "connecting-browser")
        state, client, browser, capture = self.browser_double()
        with patch.object(pipeline,"Client",client), patch.object(pipeline,"Browser",browser), patch.object(pipeline,"capture",capture):
            pipeline.execute(job,self.config)
        self.assertEqual(job.data["preparation"],frozen)
        self.assertEqual(job.data["state"],"complete")
        self.assertEqual(state["sent"],1)

    def test_connection_resume_never_replays_started_ui_work(self):
        job = self.submit()
        job.update(stage="prepared-materials",browser_phase="connected")
        with self.assertRaisesRegex(BridgeError,"will not be replayed"):
            pipeline.execute(job,self.config)

    def test_claim_contention_resume_reuses_preparation(self):
        job = self.submit()
        with patch.object(pipeline, "acquire_browser_lease", side_effect=BridgeError("claim conflict")), \
                patch.object(pipeline, "Client") as blocked_client:
            with self.assertRaisesRegex(BridgeError, "claim conflict"):
                pipeline.execute(job, self.config)
            blocked_client.assert_not_called()
        frozen = dict(job.data["preparation"])
        self.assertEqual(job.data["browser_phase"], "acquiring-claim")
        state, client, browser, capture = self.browser_double()
        with patch.multiple(pipeline, Client=client, Browser=browser, capture=capture):
            pipeline.execute(job, self.config)
        self.assertEqual(job.data["preparation"], frozen)
        self.assertEqual(job.data["state"], "complete")
        self.assertEqual(state["sent"], 1)

    def test_copied_submission_requires_exact_raw_prompt(self):
        from unittest.mock import Mock
        prompt = "Read `payload.md`"
        attempt = {"prompt": prompt, "prompt_sha256": digest(prompt), "tab_owner_token": "owner",
                   "project_id": "", "conversation_id": "", "pre_submit_boundary": "new-conversation"}
        obs = {"owner":"owner", "url":"https://chatgpt.com/c/copied-chat",
               "messages":[{"role":"user", "id":"user-1", "text":"Read payload.md"}]}
        with self.assertRaisesRegex(BridgeError, "no resend"):
            pipeline.observed_submission(obs, attempt)
        browser = Mock(owner="owner")
        browser.assert_identity = Mock()
        browser.messages.return_value = obs
        with patch("capture_copied_reply.read_windows_clipboard", side_effect=["old clipboard", prompt]):
            self.assertEqual(pipeline.verify_copied_submission(browser, obs, attempt, self.root, self.config), "user-1")
        self.assertEqual((self.root / "copied-user-prompt.md").read_text(), prompt)
        browser.evaluate.assert_called_once()
        with patch("capture_copied_reply.read_windows_clipboard", side_effect=["old clipboard", "wrong raw text"]):
            with self.assertRaisesRegex(BridgeError, "differs from frozen"):
                pipeline.verify_copied_submission(browser, obs, attempt, self.root, self.config)

    def test_round_uses_real_preflight_and_canonical_capture(self):
        job = self.submit()
        state, client, browser, capture = self.browser_double()
        with patch.multiple(pipeline, Client=client, Browser=browser, capture=capture):
            pipeline.execute(job, self.config)
        self.assertEqual(state["sent"], 1)
        result = self.jobs.result(job.data["job_id"])
        self.assertEqual(result["state"], "complete")
        self.assertTrue(Path(result["result"]["turn_path"]).is_file())
        self.assertIsNone(active_attempt(self.repo, "runtime-test"))
        Path(result["result"]["answer_path"]).write_text("drift", encoding="utf-8")
        with self.assertRaisesRegex(BridgeError, "digest drift"):
            self.jobs.result(job.data["job_id"])

    def test_disconnect_after_send_recovers_without_resend(self):
        job = self.submit()
        state, client, browser, capture = self.browser_double(crash_after_send=True)
        with patch.multiple(pipeline, Client=client, Browser=browser, capture=capture):
            with self.assertRaisesRegex(RuntimeError, "injected disconnect"):
                pipeline.execute(job, self.config)
            self.assertEqual(active_attempt(self.repo, "runtime-test")["state"], "send-started")
            pipeline.execute(Job(job.directory), self.config)
        self.assertEqual(state["sent"], 1)
        self.assertEqual(self.jobs.result(job.data["job_id"])["state"], "complete")

    def test_unknown_send_that_never_arrived_is_still_not_replayed(self):
        job = self.submit()
        state, client, browser, capture = self.browser_double(crash_after_send="before")
        with patch.multiple(pipeline, Client=client, Browser=browser, capture=capture):
            with self.assertRaisesRegex(RuntimeError, "before click"):
                pipeline.execute(job, self.config)
            self.assertEqual(active_attempt(self.repo, "runtime-test")["state"], "send-started")
            clock = iter((0, 0, 100, 200))
            with patch.object(pipeline.time, "monotonic", side_effect=lambda: next(clock)), self.assertRaises(BridgeError):
                pipeline.execute(Job(job.directory), self.config)
        self.assertEqual(state["sent"], 0)


if __name__ == "__main__":
    unittest.main()
