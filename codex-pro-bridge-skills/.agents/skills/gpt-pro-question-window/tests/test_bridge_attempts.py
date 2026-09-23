from __future__ import annotations

import datetime as dt
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest

SHARED = Path(__file__).resolve().parents[2] / ".shared"
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path.insert(0, str(SHARED))
from bridge_attempts import (
    active_attempt, assert_preflight_allowed, browser_wait_script, browser_wait_exec,
    complete_attempt, digest, fail_attempt,
    mark_send_started, prepare_attempt, read_attempt, record_submission, recovery_guard,
    register_watcher, reserve_watcher, stop_watcher_record, validate_capture, wait_plan,
)
from bridge_store import BridgeError, acquire_browser_lease, bridge_root, load_events
from browser_host import staging_windows_root


class AttemptTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.repo = Path(self.temp.name).resolve()
        self.thread = "test-attempt"
        self.prompt = "请核对这个局部问题。\n"
        self.url = "https://chatgpt.com/c/test-conversation"
        self.deadline = (dt.datetime.now().astimezone() + dt.timedelta(hours=2)).isoformat()
        self.preflight = {
            "ready": True, "bridge_thread_id": self.thread,
            "browser_claim_verification": "verified", "turn_boundary_verification": "verified",
            "prompt_sha256": digest(self.prompt), "pre_submit_boundary": "previous-turn",
            "tab_owner_token": "owner-a", "expected_conversation_id": "test-conversation",
            "observed_page_url": self.url,
        }
        self.browser = {"available_tools": ["browser-read"], "browser_read_tool": "browser-read"}

    def prepare(self):
        self.attempt = prepare_attempt(self.repo, self.thread, self.prompt, self.preflight, self.deadline)
        self.aid = self.attempt["attempt_id"]
        return self.attempt

    def submit(self):
        self.prepare()
        mark_send_started(self.repo, self.thread, self.aid)
        return record_submission(self.repo, self.thread, self.aid, conversation_url=self.url,
            owner=self.preflight["tab_owner_token"], prompt_sha256=digest(self.prompt),
            boundary="previous-turn", remote_turn_id="user-turn-1", after_boundary="yes")

    def run_cli(self, script, *args, expected=0):
        result = subprocess.run([sys.executable, str(script), *map(str, args)], cwd=self.repo,
                                env={**os.environ, "PYTHONDONTWRITEBYTECODE": "1",
                                     "CODEX_PRO_BRIDGE_BROWSER_STATE_DIR": str(self.repo / "browser-state")},
                                capture_output=True, text=True)
        self.assertEqual(result.returncode, expected, result.stderr)
        return result

    def mcp_temp_root(self):
        if os.name == "nt":
            return self.repo / ".codex" / "codex-pro-bridge" / "browser-staging"
        return staging_windows_root()

    def test_send_intent_survives_process_restart_and_blocks_empty_composer(self):
        self.prepare()
        self.run_cli(SCRIPTS / "manage_bridge_attempt.py", "--repo", self.repo,
                     "--bridge-thread-id", self.thread, "send-started", "--attempt-id", self.aid)
        a = read_attempt(self.repo, self.thread, self.aid)
        self.assertEqual(a["state"], "send-started")
        with self.assertRaises(BridgeError):
            mark_send_started(self.repo, self.thread, self.aid)
        with self.assertRaises(BridgeError):
            recovery_guard(self.repo, self.thread, self.aid, prompt_sha256=digest(self.prompt),
                boundary="previous-turn", owner="owner-a", composer_state="empty")
        with self.assertRaises(BridgeError):
            assert_preflight_allowed(self.repo, self.thread, digest(self.prompt), "previous-turn", "owner-a")
        with self.assertRaises(BridgeError):
            self.prepare()

    def test_no_scheduler_uses_live_session_and_wait_does_not_change_state(self):
        a = self.submit()
        before = read_attempt(self.repo, self.thread, self.aid)
        plan = wait_plan(a, self.browser)
        self.assertEqual(plan["mode"], "active-session")
        self.assertTrue(plan["requires_live_session"])
        self.assertFalse(plan["background_registered"])
        self.assertFalse(plan["can_register_watcher"])
        self.assertEqual(before, read_attempt(self.repo, self.thread, self.aid))
        capabilities_file = self.repo / "capabilities.json"
        capabilities_file.write_text(json.dumps(self.browser), encoding="utf-8")
        result = self.run_cli(SCRIPTS / "manage_bridge_attempt.py", "--repo", self.repo,
            "--bridge-thread-id", self.thread, "wait-plan", "--attempt-id", self.aid,
            "--capabilities-file", capabilities_file)
        self.assertEqual(json.loads(result.stdout)["mode"], "active-session")
        self.assertEqual(wait_plan(a, {"available_tools": []})["mode"], "manual")
        with self.assertRaises(BridgeError):
            reserve_watcher(self.repo, self.thread, self.aid, self.browser)
        with self.assertRaises(BridgeError):
            wait_plan(a, {"available_tools": [], "chatgpt_read_tool": "imaginary-read-thread"})

    def test_missing_business_deadline_is_continuable_and_cli_optional(self):
        attempt = prepare_attempt(self.repo, self.thread, self.prompt, self.preflight)
        self.assertIsNone(attempt["deadline"])
        mark_send_started(self.repo, self.thread, attempt["attempt_id"])
        submitted = record_submission(
            self.repo,
            self.thread,
            attempt["attempt_id"],
            conversation_url=self.url,
            owner="owner-a",
            prompt_sha256=digest(self.prompt),
            boundary="previous-turn",
            remote_turn_id="user-turn-no-deadline",
            after_boundary="yes",
        )
        plan = wait_plan(submitted, self.browser)
        self.assertEqual(plan["action"], "read-exact-turn")
        self.assertEqual(plan["mode"], "active-session")
        self.assertIsNone(plan["deadline"])
        checkpoint = bridge_root(self.repo) / "attempts" / self.thread / (attempt["attempt_id"] + ".json")
        legacy_checkpoint = json.loads(checkpoint.read_text(encoding="utf-8"))
        legacy_checkpoint.pop("deadline")
        checkpoint.write_text(json.dumps(legacy_checkpoint), encoding="utf-8")
        legacy_loaded = read_attempt(self.repo, self.thread, attempt["attempt_id"])
        self.assertIsNone(wait_plan(legacy_loaded, self.browser)["deadline"])
        self.assertIn('"deadline": null', browser_wait_script(legacy_loaded)["function"])
        legacy_without_deadline = {key: value for key, value in submitted.items() if key != "deadline"}
        legacy_plan = wait_plan(legacy_without_deadline, self.browser)
        self.assertIsNone(legacy_plan["deadline"])
        self.assertIn('"deadline": null', browser_wait_script(legacy_without_deadline)["function"])

        repo = self.repo / "cli-no-deadline"
        repo.mkdir()
        prompt_file = repo / "prompt.md"
        prompt_file.write_text(self.prompt, encoding="utf-8")
        preflight_file = repo / "preflight.json"
        preflight_file.write_text(json.dumps({**self.preflight, "bridge_thread_id": "cli-thread"}), encoding="utf-8")
        result = self.run_cli(
            SCRIPTS / "manage_bridge_attempt.py",
            "--repo",
            repo,
            "--bridge-thread-id",
            "cli-thread",
            "prepare",
            "--prompt-file",
            prompt_file,
            "--preflight-file",
            preflight_file,
        )
        self.assertIsNone(json.loads(result.stdout)["deadline"])

    def test_explicit_business_deadline_still_returns_record_timeout(self):
        attempt = self.submit()
        expired = {**attempt, "deadline": (dt.datetime.now().astimezone() - dt.timedelta(seconds=1)).isoformat()}
        plan = wait_plan(expired, self.browser)
        self.assertEqual(plan["action"], "record-timeout")
        self.assertEqual(plan["mode"], "terminal")

    def test_pinned_turn_cannot_move_to_another_turn_or_conversation(self):
        self.submit()
        for url, turn, sha, relation in (("https://chatgpt.com/c/other", "user-turn-1", digest(self.prompt), "yes"),
                                       (self.url, "turn-other", digest(self.prompt), "yes"),
                                       (self.url, "user-turn-1", "0" * 64, "yes"),
                                       (self.url, "user-turn-1", digest(self.prompt), "no")):
            with self.subTest(url=url, turn=turn, relation=relation), self.assertRaises(BridgeError):
                record_submission(self.repo, self.thread, self.aid, conversation_url=url,
                    owner="owner-a", prompt_sha256=sha, boundary="previous-turn", remote_turn_id=turn,
                    after_boundary=relation)

    def test_passive_wait_uses_pinned_identity_without_changing_attempt(self):
        a = self.submit()
        inventory = {"available_tools": ["evaluate_script"], "browser_wait_tool": "evaluate_script"}
        plan = wait_plan(a, inventory)
        self.assertEqual(plan["action"], "wait-exact-turn")
        self.assertNotIn("next_check_after_seconds", plan)
        self.assertFalse(plan["background_registered"])
        result = self.run_cli(SCRIPTS / "manage_bridge_attempt.py", "--repo", self.repo,
            "--bridge-thread-id", self.thread, "wait-script", "--attempt-id", self.aid,
            "--observed-page-url", self.url)
        source = json.loads(result.stdout)["function"]
        self.assertIn("user-turn-1", source)
        self.assertNotIn(self.prompt.strip(), source)
        self.assertEqual(a, read_attempt(self.repo, self.thread, self.aid))
        with self.assertRaises(BridgeError):
            browser_wait_script({**a, "state": "send-started"})
        with self.assertRaises(BridgeError):
            browser_wait_script(a, "https://chatgpt.com/c/another")
        project = "g-p-1234"
        changed = browser_wait_script({**a, "project_id": project},
                                     f"https://chatgpt.com/g/{project}-renamed/c/test-conversation")
        self.assertIn(project + "-renamed", changed["function"])
        supported = {**inventory, "available_tools": ["evaluate_script", "create", "stop"],
                     "watcher_create_tool": "create", "watcher_stop_tool": "stop"}
        self.assertTrue(wait_plan(a, supported)["can_register_watcher"])
        self.assertEqual(reserve_watcher(self.repo, self.thread, self.aid, supported)["watcher"]["state"],
                         "registering")

    def test_wait_runner_consumes_empty_batches_and_surfaces_tool_errors(self):
        a = self.submit()
        source = browser_wait_exec(a, "7", self.url)
        harness = r'''
const fs = require('node:fs');
const source = fs.readFileSync(0, 'utf8');
const mode = process.argv[1];
let calls = 0;
const outputs = [], requests = [];
const tools = {mcp__chrome_devtools__evaluate_script: async request => {
  calls++; requests.push(JSON.stringify(request));
  if (mode === 'error') return {isError: true, content: [{type: 'text', text: 'disconnected'}]};
  if (mode === 'unknown') return {content: [{type: 'text', text: 'format changed'}]};
  const status = calls < 5 ? 'pending' : 'ready-for-capture';
  return {content: [{type: 'text', text: 'Script ran on page and returned:\n```json\n' +
    JSON.stringify({status, assistant_turn_id: 'answer'}) + '\n```'}]};
}};
new Function('tools', 'text', 'return (async () => {' + source + '})();')(tools, x => outputs.push(x))
  .then(() => process.stdout.write(JSON.stringify({calls, outputs, unique: new Set(requests).size})))
  .catch(error => process.stdout.write(JSON.stringify({calls, outputs, error: error.message})));
'''
        for mode in ["ready", "error", "unknown"]:
            with self.subTest(mode=mode):
                proc = subprocess.run(["node", "-e", harness, mode], input=source,
                                      capture_output=True, text=True, timeout=10)
                self.assertEqual(proc.returncode, 0, proc.stderr)
                out = json.loads(proc.stdout)
                if mode == "ready":
                    self.assertEqual(out["calls"], 5)
                    self.assertEqual(out["unique"], 1)
                    self.assertEqual([x["status"] for x in out["outputs"]], ["ready-for-capture"])
                else:
                    self.assertEqual(out["calls"], 1)
                    self.assertEqual(out["outputs"], [])
                    self.assertTrue(out["error"])
        emitted = self.run_cli(SCRIPTS / "manage_bridge_attempt.py", "--repo", self.repo,
            "--bridge-thread-id", self.thread, "wait-script", "--attempt-id", self.aid,
            "--observed-page-url", self.url, "--exec-page-id", "7")
        self.assertEqual(emitted.stdout.strip(), source.strip())
        self.assertEqual(a, read_attempt(self.repo, self.thread, self.aid))

    def test_watcher_reservation_is_durable_and_cannot_duplicate(self):
        self.submit()
        inventory = {**self.browser, "available_tools": ["browser-read", "create", "stop"],
                     "watcher_create_tool": "create", "watcher_stop_tool": "stop"}
        reserved = reserve_watcher(self.repo, self.thread, self.aid, inventory)
        self.assertEqual(wait_plan(reserved, inventory)["action"], "reconcile-watcher-registration")
        with self.assertRaises(BridgeError):
            reserve_watcher(self.repo, self.thread, self.aid, inventory)
        registered = register_watcher(self.repo, self.thread, self.aid, "real-automation-id")
        self.assertEqual(wait_plan(registered, inventory)["mode"], "scheduled")
        failed = fail_attempt(self.repo, self.thread, self.aid, "remote failure observed")
        self.assertEqual(wait_plan(failed, inventory)["action"], "stop-watcher")
        with self.assertRaises(BridgeError):
            stop_watcher_record(self.repo, self.thread, self.aid, "wrong-id")
        stopped = stop_watcher_record(self.repo, self.thread, self.aid, "real-automation-id")
        self.assertEqual(wait_plan(stopped, inventory)["action"], "terminal")

    def test_timed_out_send_cannot_be_replaced_with_a_new_attempt(self):
        self.submit()
        fail_attempt(self.repo, self.thread, self.aid, "local wait deadline reached; remote outcome unknown")
        self.assertIsNotNone(active_attempt(self.repo, self.thread))
        with self.assertRaises(BridgeError):
            self.prepare()
        with self.assertRaises(BridgeError):
            assert_preflight_allowed(self.repo, self.thread, digest(self.prompt), "previous-turn", "owner-a")
        # A later verified observation may still recover the original result.
        recovered = record_submission(self.repo, self.thread, self.aid, conversation_url=self.url,
            owner="owner-a", prompt_sha256=digest(self.prompt), boundary="previous-turn",
            remote_turn_id="user-turn-1", after_boundary="yes")
        self.assertEqual(recovered["state"], "submitted")

    def test_complete_requires_canonical_capture(self):
        self.submit()
        arbitrary = self.repo / "unrelated.md"
        arbitrary.write_text("not a captured turn", encoding="utf-8")
        with self.assertRaises(BridgeError):
            complete_attempt(self.repo, self.thread, self.aid, arbitrary)
        with self.assertRaises(BridgeError):
            validate_capture(self.repo, self.thread, self.aid, prompt=self.prompt,
                conversation_url=self.url, remote_turn_id="other-turn")

    def test_full_local_cli_lifecycle_and_capture_retry(self):
        notes = SHARED.parent / "bundle-algorithm-context/scripts/prepare_codex_session_notes.py"
        self.run_cli(notes, "--repo", self.repo, "--bridge-thread-id", self.thread,
                     "--goal", "isolated recovery test", "--gpt-pro-question", self.prompt,
                     "--summary", "fixture evidence")
        # Use the real registry/identity gate with synthetic browser observations only.
        original = os.environ.get("CODEX_PRO_BRIDGE_BROWSER_STATE_DIR")
        os.environ["CODEX_PRO_BRIDGE_BROWSER_STATE_DIR"] = str(self.repo / "browser-state")
        try:
            claim = acquire_browser_lease(self.repo, holder="test", thread_id=self.thread,
                                          expected_conversation_id="test-conversation")
        finally:
            if original is None:
                os.environ.pop("CODEX_PRO_BRIDGE_BROWSER_STATE_DIR", None)
            else:
                os.environ["CODEX_PRO_BRIDGE_BROWSER_STATE_DIR"] = original
        owner = claim["tab_owner_token"]
        pages = json.dumps([{"page_id": "7", "url": self.url}])
        shared_args = ["--repo", self.repo, "--bridge-thread-id", self.thread,
            "--browser-lease-token", claim["token"], "--expected-conversation-id", "test-conversation",
            "--observed-page-url", self.url, "--matching-page-count", "1", "--pages-json", pages,
            "--observed-page-id", "7", "--snapshot-page-id", "7", "--observed-tab-owner-token", owner,
            "--pre-submit-boundary", "previous-turn"]
        pre = self.run_cli(SCRIPTS / "check_browser_preflight.py", *shared_args,
            "--observed-conversation-id", "test-conversation", "--prompt-sha256", digest(self.prompt),
            "--requested-model", "GPT-5.6 Sol", "--selected-ui-label", "GPT-5.6 Sol",
            "--requested-thinking-intensity", "6 Pro", "--selected-thinking-intensity", "6 Pro",
            "--mcp-host-os", "windows", "--browser-host-os", "windows", "--mcp-temp-root", self.mcp_temp_root())
        self.preflight = json.loads(pre.stdout)
        preflight_file = self.repo / "preflight.json"
        preflight_file.write_text(pre.stdout, encoding="utf-8")
        prompt_file = self.repo / "prompt.txt"
        prompt_file.write_text(self.prompt, encoding="utf-8")
        prepared = self.run_cli(SCRIPTS / "manage_bridge_attempt.py", "--repo", self.repo,
            "--bridge-thread-id", self.thread, "prepare", "--prompt-file", prompt_file,
            "--preflight-file", preflight_file, "--deadline", self.deadline)
        self.aid = json.loads(prepared.stdout)["attempt_id"]
        mark_send_started(self.repo, self.thread, self.aid)
        recovery_args = [*shared_args, "--expected-prompt-sha256", digest(self.prompt),
                         "--owners-json", json.dumps([{"page_id": "7", "owner_token": owner}])]
        empty = self.run_cli(SCRIPTS / "check_browser_recovery.py", *recovery_args,
                            "--composer-state", "empty", expected=2)
        self.assertIn("never authorizes resubmission", empty.stderr)
        self.run_cli(SCRIPTS / "check_browser_recovery.py", *recovery_args, "--composer-state", "completed",
            "--observed-user-turn-id", "user-turn-1", "--observed-user-turn-sha256", digest(self.prompt),
            "--observed-user-turn-after-boundary", "yes")
        prompt_file = self.repo / "prompt.txt"
        answer_file = self.repo / "answer.txt"
        prompt_file.write_text(self.prompt, encoding="utf-8")
        answer_file.write_text("完整回答。\n", encoding="utf-8")
        capture_args = ["--repo", self.repo, "--bridge-thread-id", self.thread, "--standalone",
            "--web-url", self.url, "--single-round", "--attempt-id", self.aid, "--capture-route", "native-read-thread",
            "--expected-conversation-id", "test-conversation", "--remote-turn-id", "user-turn-1",
            "--prompt-file", prompt_file, "--answer-file", answer_file,
            "--response-completed-at", dt.datetime.now().astimezone().isoformat()]
        crash_code = (
            "import sys,runpy; from unittest.mock import patch\n"
            f"sys.path.insert(0, {str(SHARED)!r})\n"
            "import bridge_attempts\n"
            "with patch.object(bridge_attempts, 'complete_attempt', side_effect=bridge_attempts.BridgeError('simulated checkpoint interruption')):\n"
            f"    runpy.run_path({str(SCRIPTS / 'save_bridge_turn.py')!r}, run_name='__main__')\n"
        )
        crashed = self.run_cli("-c", crash_code, *capture_args, expected=2)
        self.assertIn("simulated checkpoint interruption", crashed.stderr)
        self.assertEqual(read_attempt(self.repo, self.thread, self.aid)["state"], "submitted")
        self.assertEqual(sum(e["event_type"] == "gpt-exchange" for e in load_events(bridge_root(self.repo), self.thread)), 1)
        answer_file.write_text("different answer", encoding="utf-8")
        self.run_cli(SCRIPTS / "save_bridge_turn.py", *capture_args, expected=2)
        answer_file.write_text("完整回答。\n", encoding="utf-8")
        first = self.run_cli(SCRIPTS / "save_bridge_turn.py", *capture_args)
        turn_text = Path(first.stdout.strip()).read_text(encoding="utf-8")
        self.assertIn("- Answer Format: native-raw", turn_text)
        answer_digest = digest("完整回答。\n")
        self.assertIn(f"- Answer SHA-256: {answer_digest}", turn_text)
        capture_args[-1] = dt.datetime.now().astimezone().isoformat()
        second = self.run_cli(SCRIPTS / "save_bridge_turn.py", *capture_args)
        self.assertEqual(first.stdout, second.stdout)
        captured = read_attempt(self.repo, self.thread, self.aid)
        self.assertEqual(captured["state"], "captured")
        self.assertEqual(captured["submitted_at"], "")
        self.assertEqual(sum(e["event_type"] == "gpt-exchange" for e in load_events(bridge_root(self.repo), self.thread)), 1)
        answer_file.write_text("different answer", encoding="utf-8")
        self.run_cli(SCRIPTS / "save_bridge_turn.py", *capture_args, expected=2)
        self.assertEqual(sum(e["event_type"] == "gpt-exchange" for e in load_events(bridge_root(self.repo), self.thread)), 1)


if __name__ == "__main__":
    unittest.main()
