from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

SHARED_DIR = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED_DIR))

from bridge_store import (
    BridgeError,
    acquire_browser_lease,
    read_browser_lease,
    release_browser_lease,
)
from browser_host import staging_windows_root
from browser_identity import (
    match_listed_pages,
    prepare_bootstrap_tab,
    promote_bootstrap_tab,
    resolve_owned_tab,
    verify_bootstrap_tab,
)


class BrowserBootstrapTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name).resolve() / "repo"
        self.repo.mkdir()
        self.previous_state_dir = os.environ.get("CODEX_PRO_BRIDGE_BROWSER_STATE_DIR")
        os.environ["CODEX_PRO_BRIDGE_BROWSER_STATE_DIR"] = str(
            Path(self.temp.name).resolve() / "browser-state"
        )

    def tearDown(self) -> None:
        if self.previous_state_dir is None:
            os.environ.pop("CODEX_PRO_BRIDGE_BROWSER_STATE_DIR", None)
        else:
            os.environ["CODEX_PRO_BRIDGE_BROWSER_STATE_DIR"] = self.previous_state_dir
        self.temp.cleanup()

    def test_project_bootstrap_promotes_to_durable_conversation(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="worker-a",
            thread_id="thread-a",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        self.assertTrue(claim["bootstrap"])
        self.assertEqual(claim["scope"], "project")
        verified = verify_bootstrap_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="thread-a",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            observed_page_url="https://chatgpt.com/g/g-p-demo-title/project",
            matching_page_count=1,
            observed_page_id="7",
            snapshot_page_id="7",
            observed_tab_owner_token=claim["tab_owner_token"],
        )
        self.assertEqual(verified["claim"]["token"], claim["token"])

        script = Path(__file__).resolve().parents[1] / "scripts" / "manage_browser_lease.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo",
                str(self.repo),
                "promote-bootstrap",
                "--token",
                claim["token"],
                "--bridge-thread-id",
                "thread-a",
                "--expected-remote-project-id",
                "g-p-demo",
                "--observed-page-url",
                "https://chatgpt.com/g/g-p-demo-title/c/conversation-1",
                "--matching-page-count",
                "1",
                "--observed-page-id",
                "7",
                "--snapshot-page-id",
                "7",
                "--observed-tab-owner-token",
                claim["tab_owner_token"],
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        promoted = json.loads(result.stdout)
        self.assertEqual(promoted["conversation_id"], "conversation-1")
        self.assertEqual(promoted["claim"]["scope"], "conversation")
        self.assertFalse(promoted["claim"]["bootstrap"])
        self.assertEqual(
            promoted["release_argv"],
            ["release", "--token", claim["token"], "--terminal-state", "send-accepted"],
        )

        release_browser_lease(self.repo, token=claim["token"])
        registry = read_browser_lease(self.repo)
        self.assertEqual(registry["leases"], [])
        self.assertEqual(len(registry["bindings"]), 1)
        self.assertEqual(
            registry["bindings"][0]["expected_conversation_id"], "conversation-1"
        )
        resumed = acquire_browser_lease(
            self.repo,
            holder="worker-a-resumed",
            thread_id="thread-a",
            expected_conversation_id="conversation-1",
            expected_remote_project_id="g-p-demo",
            scope="conversation",
        )
        self.assertEqual(resumed["tab_owner_token"], claim["tab_owner_token"])

    def test_standalone_bootstrap_uses_profile_scope(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "manage_browser_lease.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo",
                str(self.repo),
                "acquire",
                "--holder",
                "worker-b",
                "--bridge-thread-id",
                "thread-b",
                "--scope",
                "profile",
                "--bootstrap",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        claim = json.loads(result.stdout)
        verify_bootstrap_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="thread-b",
            browser_profile=claim["browser_profile"],
            expected_project_id="",
            observed_page_url="https://chatgpt.com/",
            matching_page_count=1,
            observed_page_id="3",
            snapshot_page_id="3",
            observed_tab_owner_token=claim["tab_owner_token"],
        )
        promoted = promote_bootstrap_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="thread-b",
            browser_profile=claim["browser_profile"],
            expected_project_id="",
            observed_page_url="https://chatgpt.com/c/conversation-2",
            matching_page_count=1,
            observed_page_id="3",
            snapshot_page_id="3",
            observed_tab_owner_token=claim["tab_owner_token"],
        )
        self.assertEqual(promoted["claim"]["scope"], "conversation")

    def test_wrong_project_and_second_owner_fail_closed(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="worker-a",
            thread_id="thread-a",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        with self.assertRaises(BridgeError):
            acquire_browser_lease(
                self.repo,
                holder="worker-b",
                thread_id="thread-b",
                expected_remote_project_id="g-p-demo",
                scope="project",
                bootstrap=True,
            )
        with self.assertRaises(BridgeError):
            promote_bootstrap_tab(
                self.repo,
                claim_token=claim["token"],
                thread_id="thread-a",
                browser_profile=claim["browser_profile"],
                expected_project_id="g-p-demo",
                observed_page_url="https://chatgpt.com/g/g-p-other/c/conversation-3",
                matching_page_count=1,
                observed_page_id="9",
                snapshot_page_id="9",
                observed_tab_owner_token=claim["tab_owner_token"],
            )

    def test_bootstrap_preflight_accepts_project_home_without_conversation_id(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="worker-c",
            thread_id="thread-c",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        script = Path(__file__).resolve().parents[1] / "scripts" / "check_browser_preflight.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "thread-c",
                "--browser-lease-token",
                claim["token"],
                "--conversation-bootstrap",
                "--requested-model",
                "最新",
                "--selected-ui-label",
                "最新",
                "--model-selection-kind",
                "latest-alias",
                "--requested-thinking-intensity",
                "6 Pro",
                "--selected-thinking-intensity",
                "6 Pro",
                "--mcp-host-os",
                "windows",
                "--browser-host-os",
                "windows",
                "--mcp-temp-root",
                str(staging_windows_root()),
                "--expected-project-id",
                "g-p-demo",
                "--observed-project-id",
                "g-p-demo",
                "--expected-workspace",
                "workspace-a",
                "--observed-workspace",
                "workspace-a",
                "--expected-account-label",
                "account-a",
                "--observed-account-label",
                "account-a",
                "--binding-status",
                "active",
                "--observed-page-url",
                "https://chatgpt.com/g/g-p-demo-title/project",
                "--pages-json",
                json.dumps(
                    [
                        {
                            "page_id": 11,
                            "url": "https://chatgpt.com/g/g-p-demo-title/project",
                        }
                    ]
                ),
                "--owners-json",
                json.dumps([{"pageId": 11, "owner": claim["tab_owner_token"]}]),
                "--matching-page-count",
                "1",
                "--observed-page-id",
                "11",
                "--snapshot-page-id",
                "11",
                "--observed-tab-owner-token",
                claim["tab_owner_token"],
                "--pre-submit-boundary",
                "new-conversation",
                "--prompt-sha256",
                "0" * 64,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        output = json.loads(result.stdout)
        self.assertTrue(output["ready"])
        self.assertTrue(output["conversation_bootstrap"])
        self.assertEqual(output["conversation_verification"], "bootstrap-pending")

    def test_existing_conversation_preflight_still_requires_exact_identity(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="worker-d",
            thread_id="thread-d",
            expected_conversation_id="conversation-existing",
            scope="conversation",
        )
        script = Path(__file__).resolve().parents[1] / "scripts" / "check_browser_preflight.py"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "thread-d",
                "--browser-lease-token",
                claim["token"],
                "--requested-model",
                "GPT-5.6 Sol",
                "--selected-ui-label",
                "GPT-5.6 Sol",
                "--requested-thinking-intensity",
                "6 Pro",
                "--selected-thinking-intensity",
                "6 Pro",
                "--mcp-host-os",
                "windows",
                "--browser-host-os",
                "windows",
                "--mcp-temp-root",
                str(staging_windows_root()),
                "--expected-conversation-id",
                "conversation-existing",
                "--observed-conversation-id",
                "conversation-existing",
                "--observed-page-url",
                "https://chatgpt.com/c/conversation-existing",
                "--pages-json",
                json.dumps(
                    [
                        {
                            "page_id": 12,
                            "url": "https://chatgpt.com/c/conversation-existing",
                        }
                    ]
                ),
                "--owners-json",
                json.dumps([{"pageId": 12, "owner": claim["tab_owner_token"]}]),
                "--matching-page-count",
                "1",
                "--observed-page-id",
                "12",
                "--snapshot-page-id",
                "12",
                "--observed-tab-owner-token",
                claim["tab_owner_token"],
                "--pre-submit-boundary",
                "turn-before-send",
                "--prompt-sha256",
                "1" * 64,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        output = json.loads(result.stdout)
        self.assertFalse(output["conversation_bootstrap"])
        self.assertEqual(output["conversation_verification"], "verified")

    def test_prepare_bootstrap_tab_reuses_or_reclaims_unique_canonical_tab(self) -> None:
        stale = acquire_browser_lease(
            self.repo,
            holder="old-worker",
            thread_id="old-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        release_browser_lease(self.repo, token=stale["token"])
        claim = acquire_browser_lease(
            self.repo,
            holder="new-worker",
            thread_id="new-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        common = {
            "repo": self.repo,
            "claim_token": claim["token"],
            "thread_id": "new-thread",
            "browser_profile": claim["browser_profile"],
            "expected_project_id": "g-p-demo",
            "matching_page_count": 1,
            "observed_page_url": "https://chatgpt.com/g/g-p-demo-title/project",
            "observed_page_id": "17",
        }
        self.assertEqual(
            prepare_bootstrap_tab(**common, observed_tab_owner_token="")["action"],
            "set-owner-token",
        )
        reclaimed = prepare_bootstrap_tab(
            **common, observed_tab_owner_token=stale["tab_owner_token"]
        )
        self.assertEqual(reclaimed["action"], "replace-stale-owner-token")
        self.assertEqual(reclaimed["previous_owner_status"], "stale")
        self.assertFalse(reclaimed["tab_bound"])
        reused = prepare_bootstrap_tab(
            **common, observed_tab_owner_token=claim["tab_owner_token"]
        )
        self.assertEqual(reused["action"], "reuse-owned-tab")
        self.assertTrue(reused["tab_bound"])

    def test_prepare_bootstrap_tab_opens_only_when_no_match_and_rejects_duplicates(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="worker-open",
            thread_id="thread-open",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        base = {
            "repo": self.repo,
            "claim_token": claim["token"],
            "thread_id": "thread-open",
            "browser_profile": claim["browser_profile"],
            "expected_project_id": "g-p-demo",
        }
        planned = prepare_bootstrap_tab(**base, matching_page_count=0)
        self.assertEqual(planned["action"], "open-canonical-tab")
        self.assertEqual(
            planned["canonical_url"], "https://chatgpt.com/g/g-p-demo/project"
        )
        with self.assertRaises(BridgeError):
            prepare_bootstrap_tab(**base, matching_page_count=2)

    def test_prepare_bootstrap_tab_protects_live_and_durable_owners(self) -> None:
        protected = acquire_browser_lease(
            self.repo,
            holder="protected-worker",
            thread_id="protected-thread",
            expected_remote_project_id="g-p-other",
            scope="project",
            bootstrap=True,
        )
        claim = acquire_browser_lease(
            self.repo,
            holder="current-worker",
            thread_id="current-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        live_result = prepare_bootstrap_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="current-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            matching_page_count=1,
            observed_page_url="https://chatgpt.com/g/g-p-demo/project",
            observed_page_id="18",
            observed_tab_owner_token=protected["tab_owner_token"],
        )
        self.assertEqual(live_result["action"], "hold")
        self.assertIn("live-claim", live_result["reason"])

        release_browser_lease(self.repo, token=protected["token"])
        conversation = acquire_browser_lease(
            self.repo,
            holder="bound-worker",
            thread_id="bound-thread",
            expected_conversation_id="conversation-bound",
            expected_remote_project_id="g-p-other",
            scope="conversation",
        )
        release_browser_lease(self.repo, token=conversation["token"])
        durable_result = prepare_bootstrap_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="current-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            matching_page_count=1,
            observed_page_url="https://chatgpt.com/g/g-p-demo/project",
            observed_page_id="18",
            observed_tab_owner_token=conversation["tab_owner_token"],
        )
        self.assertEqual(durable_result["action"], "hold")
        self.assertIn("durable-binding", durable_result["reason"])

    def test_bootstrap_destination_rejects_noncanonical_query(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="worker-query",
            thread_id="thread-query",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        with self.assertRaisesRegex(BridgeError, "disagrees"):
            prepare_bootstrap_tab(
                self.repo,
                claim_token=claim["token"],
                thread_id="thread-query",
                browser_profile=claim["browser_profile"],
                expected_project_id="g-p-demo",
                matching_page_count=1,
                observed_page_url="https://chatgpt.com/g/g-p-demo/project?token=audit",
                observed_page_id="19",
                observed_tab_owner_token="",
            )

    def test_cli_prepare_and_bootstrap_release_require_cleanup_state(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "manage_browser_lease.py"
        acquired = subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo",
                str(self.repo),
                "acquire",
                "--holder",
                "cli-worker",
                "--bridge-thread-id",
                "cli-thread",
                "--expected-remote-project-id",
                "g-p-demo",
                "--scope",
                "project",
                "--bootstrap",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        claim = json.loads(acquired.stdout)
        prepared = subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo",
                str(self.repo),
                "prepare-bootstrap-tab",
                "--token",
                claim["token"],
                "--bridge-thread-id",
                "cli-thread",
                "--expected-remote-project-id",
                "g-p-demo",
                "--matching-page-count",
                "0",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        self.assertEqual(json.loads(prepared.stdout)["action"], "open-canonical-tab")
        refused = subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo",
                str(self.repo),
                "release",
                "--token",
                claim["token"],
            ],
            check=False,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        self.assertNotEqual(refused.returncode, 0)
        self.assertIn("--bootstrap-tab-cleanup", refused.stderr)
        released = subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo",
                str(self.repo),
                "release",
                "--token",
                claim["token"],
                "--bootstrap-tab-cleanup",
                "owner-token-never-bound",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        self.assertTrue(json.loads(released.stdout)["released"])

    def test_full_page_resolution_counts_in_python_and_never_opens_existing_chat(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="conversation-worker",
            thread_id="conversation-thread",
            expected_conversation_id="conversation-exact",
            expected_remote_project_id="g-p-demo",
            scope="conversation",
        )
        pages = [
            {"page_id": 1, "url": "https://grok.com/c/not-chatgpt"},
            {
                "page_id": 2,
                "url": "https://chatgpt.com/g/g-p-demo-title/c/conversation-exact",
            },
        ]
        matched = match_listed_pages(
            pages,
            expected_project_id="g-p-demo",
            expected_conversation_id="conversation-exact",
        )
        self.assertEqual(len(matched["conversation_matches"]), 1)
        need_owner = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="conversation-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=pages,
        )
        self.assertEqual(need_owner["action"], "read-owners-on")
        self.assertEqual(need_owner["read_owners_on"], ["2"])
        missing = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="conversation-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=[],
            owners=[],
        )
        self.assertEqual(missing["action"], "hold")
        self.assertEqual(missing["reason"], "owned-tab-not-listed")
        moved_pages = pages + [{"page_id": 3, "url": "https://chatgpt.com/c/wrong-chat"}]
        moved = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="conversation-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=moved_pages,
            owners=[
                {"page_id": 2, "owner_token": ""},
                {"page_id": 3, "owner_token": claim["tab_owner_token"]},
            ],
        )
        self.assertEqual(moved["action"], "hold")
        self.assertEqual(moved["reason"], "owned-tab-url-mismatch")
        with self.assertRaisesRegex(BridgeError, "invalid URL"):
            match_listed_pages(
                [{"page_id": 4, "url": "not-a-url"}],
                expected_project_id="g-p-demo",
                expected_conversation_id="conversation-exact",
            )

    def test_bootstrap_reads_all_chatgpt_owners_before_opening(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="bootstrap-reader",
            thread_id="bootstrap-reader-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        pages = [
            {"page_id": 1, "url": "https://grok.com/c/ignored"},
            {"page_id": 2, "url": "https://chatgpt.com/c/unrelated"},
        ]
        unresolved = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="bootstrap-reader-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=pages,
        )
        self.assertEqual(unresolved["action"], "read-owners-on")
        self.assertEqual(unresolved["read_owners_on"], ["2"])
        resolved = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="bootstrap-reader-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=pages,
            owners=[{"page_id": 2, "owner_token": ""}],
        )
        self.assertEqual(resolved["action"], "open-canonical-tab")
        self.assertFalse(resolved["tab_bound"])

    def test_bootstrap_rediscovery_promotes_and_bound_loss_holds(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="bootstrap-promote",
            thread_id="bootstrap-promote-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        page = {
            "page_id": 21,
            "url": "https://chatgpt.com/g/g-p-demo-title/c/conversation-created",
        }
        ready = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="bootstrap-promote-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=[page],
            owners=[{"page_id": 21, "owner_token": claim["tab_owner_token"]}],
        )
        self.assertEqual(ready["action"], "promote-ready")
        self.assertEqual(ready["conversation_id"], "conversation-created")
        self.assertTrue(ready["tab_bound"])
        lost = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="bootstrap-promote-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=[],
            owners=[],
        )
        self.assertEqual(lost["action"], "hold")
        self.assertEqual(lost["reason"], "owned-tab-not-listed")

    def test_expired_bootstrap_reacquire_preserves_owner_bound_and_promotes(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="expiry-worker",
            thread_id="expiry-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        page = {
            "page_id": 71,
            "url": "https://chatgpt.com/g/g-p-demo-title/c/expiry-conversation",
        }
        ready = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="expiry-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=[page],
            owners=[{"page_id": 71, "owner_token": claim["tab_owner_token"]}],
        )
        self.assertEqual(ready["action"], "promote-ready")

        state_path = Path(os.environ["CODEX_PRO_BRIDGE_BROWSER_STATE_DIR"]) / "ownership.json"
        registry = json.loads(state_path.read_text(encoding="utf-8"))
        registry["leases"][0]["expires_at"] = "2000-01-01T00:00:00+00:00"
        state_path.write_text(json.dumps(registry) + "\n", encoding="utf-8")

        resumed = acquire_browser_lease(
            self.repo,
            holder="expiry-worker-resumed",
            thread_id="expiry-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        self.assertNotEqual(resumed["token"], claim["token"])
        self.assertEqual(resumed["tab_owner_token"], claim["tab_owner_token"])
        self.assertTrue(resumed["tab_bound"])
        rediscovered = resolve_owned_tab(
            self.repo,
            claim_token=resumed["token"],
            thread_id="expiry-thread",
            browser_profile=resumed["browser_profile"],
            expected_project_id="g-p-demo",
            pages=[page],
            owners=[{"page_id": 71, "owner_token": claim["tab_owner_token"]}],
        )
        self.assertEqual(rediscovered["action"], "promote-ready")
        promoted = promote_bootstrap_tab(
            self.repo,
            claim_token=resumed["token"],
            thread_id="expiry-thread",
            browser_profile=resumed["browser_profile"],
            expected_project_id="g-p-demo",
            observed_page_url=page["url"],
            matching_page_count=1,
            observed_page_id="71",
            snapshot_page_id="71",
            observed_tab_owner_token=claim["tab_owner_token"],
        )
        self.assertEqual(promoted["conversation_id"], "expiry-conversation")
        self.assertEqual(read_browser_lease(self.repo)["pending_bootstraps"], [])

    def test_expired_bound_bootstrap_missing_tab_holds_and_blocks_other_thread(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="pending-worker",
            thread_id="pending-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="pending-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=[{"page_id": 72, "url": "https://chatgpt.com/g/g-p-demo/project"}],
            owners=[{"page_id": 72, "owner_token": claim["tab_owner_token"]}],
        )
        state_path = Path(os.environ["CODEX_PRO_BRIDGE_BROWSER_STATE_DIR"]) / "ownership.json"
        registry = json.loads(state_path.read_text(encoding="utf-8"))
        registry["leases"][0]["expires_at"] = "2000-01-01T00:00:00+00:00"
        state_path.write_text(json.dumps(registry) + "\n", encoding="utf-8")

        unrelated = acquire_browser_lease(
            self.repo,
            holder="unrelated-worker",
            thread_id="unrelated-thread",
            expected_remote_project_id="g-p-other",
            scope="project",
            bootstrap=True,
        )
        protected = prepare_bootstrap_tab(
            self.repo,
            claim_token=unrelated["token"],
            thread_id="unrelated-thread",
            browser_profile=unrelated["browser_profile"],
            expected_project_id="g-p-other",
            matching_page_count=1,
            observed_page_url="https://chatgpt.com/g/g-p-other/project",
            observed_page_id="74",
            observed_tab_owner_token=claim["tab_owner_token"],
        )
        self.assertEqual(protected["action"], "hold")
        self.assertIn("pending-bootstrap", protected["reason"])
        with self.assertRaisesRegex(BridgeError, "pending bootstrap"):
            acquire_browser_lease(
                self.repo,
                holder="other-worker",
                thread_id="other-thread",
                expected_remote_project_id="g-p-demo",
                scope="project",
                bootstrap=True,
            )
        with self.assertRaisesRegex(BridgeError, "pending bootstrap"):
            acquire_browser_lease(
                self.repo,
                holder="other-conversation-worker",
                thread_id="other-conversation-thread",
                expected_conversation_id="other-conversation",
                expected_remote_project_id="g-p-demo",
                scope="conversation",
            )
        resumed = acquire_browser_lease(
            self.repo,
            holder="pending-worker-resumed",
            thread_id="pending-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        missing = resolve_owned_tab(
            self.repo,
            claim_token=resumed["token"],
            thread_id="pending-thread",
            browser_profile=resumed["browser_profile"],
            expected_project_id="g-p-demo",
            pages=[],
            owners=[],
        )
        self.assertEqual(missing["action"], "hold")
        self.assertEqual(missing["reason"], "owned-tab-not-listed")

    def test_release_retires_pending_bootstrap_and_owner_becomes_stale(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="cleanup-worker",
            thread_id="cleanup-thread",
            expected_remote_project_id="g-p-cleanup",
            scope="project",
            bootstrap=True,
        )
        release_browser_lease(self.repo, token=claim["token"])
        registry = read_browser_lease(self.repo)
        self.assertEqual(registry["pending_bootstraps"], [])
        replacement = acquire_browser_lease(
            self.repo,
            holder="replacement-worker",
            thread_id="replacement-thread",
            expected_remote_project_id="g-p-cleanup",
            scope="project",
            bootstrap=True,
        )
        reclaimed = prepare_bootstrap_tab(
            self.repo,
            claim_token=replacement["token"],
            thread_id="replacement-thread",
            browser_profile=replacement["browser_profile"],
            expected_project_id="g-p-cleanup",
            matching_page_count=1,
            observed_page_url="https://chatgpt.com/g/g-p-cleanup/project",
            observed_page_id="73",
            observed_tab_owner_token=claim["tab_owner_token"],
        )
        self.assertEqual(reclaimed["action"], "replace-stale-owner-token")

    def test_expired_legacy_registry_bootstrap_is_upgraded_on_reacquire(self) -> None:
        state_path = Path(os.environ["CODEX_PRO_BRIDGE_BROWSER_STATE_DIR"]) / "ownership.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "bindings": [],
                    "leases": [
                        {
                            "token": "legacy-lease-token",
                            "holder": "legacy-worker",
                            "browser_profile": "chrome-default",
                            "scope": "project",
                            "thread_id": "legacy-expiry-thread",
                            "expected_conversation_id": "",
                            "expected_remote_project_id": "g-p-legacy",
                            "bootstrap": True,
                            "tab_owner_token": "legacy-tab-owner",
                            "storage_key": "codex-pro-bridge.tab-owner.v1",
                            "tab_bound": True,
                            "tab_bound_at": "2026-09-01T00:00:01+08:00",
                            "acquired_at": "2026-09-01T00:00:00+08:00",
                            "expires_at": "2026-09-01T00:30:00+08:00",
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        resumed = acquire_browser_lease(
            self.repo,
            holder="legacy-worker-resumed",
            thread_id="legacy-expiry-thread",
            expected_remote_project_id="g-p-legacy",
            browser_profile="chrome-stable-default",
            scope="project",
            bootstrap=True,
        )
        self.assertEqual(resumed["tab_owner_token"], "legacy-tab-owner")
        self.assertTrue(resumed["tab_bound"])
        pending = read_browser_lease(self.repo)["pending_bootstraps"]
        self.assertEqual(len(pending), 1)
        self.assertEqual(pending[0]["tab_owner_token"], "legacy-tab-owner")

    def test_unrelated_write_preserves_expired_legacy_bootstrap_identity(self) -> None:
        state_path = Path(os.environ["CODEX_PRO_BRIDGE_BROWSER_STATE_DIR"]) / "ownership.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "bindings": [],
                    "leases": [
                        {
                            "token": "legacy-retained-token",
                            "holder": "legacy-retained-worker",
                            "browser_profile": "chrome-default",
                            "scope": "project",
                            "thread_id": "legacy-retained-thread",
                            "expected_conversation_id": "",
                            "expected_remote_project_id": "g-p-legacy-retained",
                            "bootstrap": True,
                            "tab_owner_token": "legacy-retained-owner",
                            "storage_key": "codex-pro-bridge.tab-owner.v1",
                            "tab_bound": True,
                            "tab_bound_at": "2026-09-01T00:00:01+08:00",
                            "acquired_at": "2026-09-01T00:00:00+08:00",
                            "expires_at": "2026-09-01T00:30:00+08:00",
                        }
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )

        acquire_browser_lease(
            self.repo,
            holder="unrelated-new-worker",
            thread_id="unrelated-new-thread",
            expected_remote_project_id="g-p-unrelated",
            scope="project",
            bootstrap=True,
        )
        resumed = acquire_browser_lease(
            self.repo,
            holder="legacy-retained-worker-resumed",
            thread_id="legacy-retained-thread",
            expected_remote_project_id="g-p-legacy-retained",
            scope="project",
            bootstrap=True,
        )
        self.assertEqual(resumed["tab_owner_token"], "legacy-retained-owner")
        self.assertTrue(resumed["tab_bound"])
        ready = resolve_owned_tab(
            self.repo,
            claim_token=resumed["token"],
            thread_id="legacy-retained-thread",
            browser_profile=resumed["browser_profile"],
            expected_project_id="g-p-legacy-retained",
            pages=[
                {
                    "page_id": 75,
                    "url": (
                        "https://chatgpt.com/g/g-p-legacy-retained-title/c/"
                        "legacy-retained-conversation"
                    ),
                }
            ],
            owners=[{"page_id": 75, "owner_token": "legacy-retained-owner"}],
        )
        self.assertEqual(ready["action"], "promote-ready")

    def test_ambiguous_legacy_bootstrap_owners_hold(self) -> None:
        state_path = Path(os.environ["CODEX_PRO_BRIDGE_BROWSER_STATE_DIR"]) / "ownership.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        base = {
            "holder": "ambiguous-worker",
            "browser_profile": "chrome-default",
            "scope": "project",
            "thread_id": "ambiguous-thread",
            "expected_conversation_id": "",
            "expected_remote_project_id": "g-p-ambiguous",
            "bootstrap": True,
            "storage_key": "codex-pro-bridge.tab-owner.v1",
            "tab_bound": True,
            "acquired_at": "2026-09-01T00:00:00+08:00",
            "expires_at": "2026-09-01T00:30:00+08:00",
        }
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "bindings": [],
                    "leases": [
                        {**base, "token": "ambiguous-one", "tab_owner_token": "owner-one"},
                        {**base, "token": "ambiguous-two", "tab_owner_token": "owner-two"},
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        with self.assertRaisesRegex(BridgeError, "Ambiguous retained bootstrap owners"):
            acquire_browser_lease(
                self.repo,
                holder="unrelated-writer",
                thread_id="unrelated-writer-thread",
                expected_remote_project_id="g-p-other",
                scope="project",
                bootstrap=True,
            )

    def test_duplicate_owner_and_owned_wrong_url_hold(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="duplicate-worker",
            thread_id="duplicate-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        pages = [
            {"page_id": 31, "url": "https://chatgpt.com/g/g-p-demo/project"},
            {"page_id": 32, "url": "https://chatgpt.com/c/unrelated"},
        ]
        duplicate = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="duplicate-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=pages,
            owners=[
                {"page_id": 31, "owner_token": claim["tab_owner_token"]},
                {"page_id": 32, "owner_token": claim["tab_owner_token"]},
            ],
        )
        self.assertEqual(duplicate["action"], "hold")
        self.assertEqual(duplicate["reason"], "duplicate-owner")
        wrong = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="duplicate-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=[pages[1]],
            owners=[{"page_id": 32, "owner_token": claim["tab_owner_token"]}],
        )
        self.assertEqual(wrong["action"], "hold")
        self.assertEqual(wrong["reason"], "owned-tab-url-mismatch")

    def test_profile_aliases_reuse_latest_legacy_binding_and_protect_owner(self) -> None:
        state_path = Path(os.environ["CODEX_PRO_BRIDGE_BROWSER_STATE_DIR"]) / "ownership.json"
        state_path.parent.mkdir(parents=True, exist_ok=True)
        state_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "leases": [],
                    "bindings": [
                        {
                            "browser_profile": "chrome-default",
                            "thread_id": "alias-thread",
                            "expected_conversation_id": "alias-conversation",
                            "expected_remote_project_id": "g-p-alias",
                            "tab_owner_token": "older-owner",
                            "created_at": "2026-09-01T00:00:00+08:00",
                            "last_claimed_at": "2026-09-01T00:00:00+08:00",
                        },
                        {
                            "browser_profile": "chrome-devtools",
                            "thread_id": "alias-thread",
                            "expected_conversation_id": "alias-conversation",
                            "expected_remote_project_id": "g-p-alias",
                            "tab_owner_token": "newer-owner",
                            "created_at": "2026-09-02T00:00:00+08:00",
                            "last_claimed_at": "2026-09-02T00:00:00+08:00",
                        },
                    ],
                }
            )
            + "\n",
            encoding="utf-8",
        )
        conversation = acquire_browser_lease(
            self.repo,
            holder="alias-worker",
            thread_id="alias-thread",
            expected_conversation_id="alias-conversation",
            expected_remote_project_id="g-p-alias",
            browser_profile="chrome-stable-default",
            scope="conversation",
        )
        self.assertEqual(conversation["tab_owner_token"], "newer-owner")
        legacy_page = {
            "page_id": 40,
            "url": "https://chatgpt.com/g/g-p-alias/c/alias-conversation",
        }
        legacy = resolve_owned_tab(
            self.repo,
            claim_token=conversation["token"],
            thread_id="alias-thread",
            browser_profile="chrome-stable-default",
            expected_project_id="g-p-alias",
            pages=[legacy_page],
            owners=[{"page_id": 40, "owner_token": "older-owner"}],
        )
        self.assertEqual(legacy["action"], "replace-legacy-owner-token")
        duplicate = resolve_owned_tab(
            self.repo,
            claim_token=conversation["token"],
            thread_id="alias-thread",
            browser_profile="chrome-stable-default",
            expected_project_id="g-p-alias",
            pages=[
                legacy_page,
                {
                    "page_id": 42,
                    "url": "https://chatgpt.com/g/g-p-alias/c/alias-conversation",
                },
            ],
            owners=[
                {"page_id": 40, "owner_token": "older-owner"},
                {"page_id": 42, "owner_token": "newer-owner"},
            ],
        )
        self.assertEqual(duplicate["action"], "hold")
        self.assertEqual(duplicate["reason"], "duplicate-owner")
        release_browser_lease(self.repo, token=conversation["token"])
        bootstrap = acquire_browser_lease(
            self.repo,
            holder="alias-bootstrap",
            thread_id="alias-bootstrap-thread",
            expected_remote_project_id="g-p-other",
            scope="project",
            bootstrap=True,
        )
        protected = resolve_owned_tab(
            self.repo,
            claim_token=bootstrap["token"],
            thread_id="alias-bootstrap-thread",
            browser_profile="chrome-stable-default",
            expected_project_id="g-p-other",
            pages=[{"page_id": 41, "url": "https://chatgpt.com/g/g-p-other/project"}],
            owners=[{"page_id": 41, "owner_token": "older-owner"}],
        )
        self.assertEqual(protected["action"], "hold")
        self.assertIn("durable-binding", protected["reason"])
        with self.assertRaisesRegex(BridgeError, "Unknown browser profile"):
            acquire_browser_lease(
                self.repo,
                holder="unknown-profile-worker",
                thread_id="unknown-profile-thread",
                expected_remote_project_id="g-p-unknown",
                browser_profile="unknown-profile",
                scope="project",
                bootstrap=True,
            )

    def test_cli_resolve_tab_and_recovery_promote_ready(self) -> None:
        script = Path(__file__).resolve().parents[1] / "scripts" / "manage_browser_lease.py"
        claim = acquire_browser_lease(
            self.repo,
            holder="cli-resolver",
            thread_id="cli-resolver-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        opened = subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo",
                str(self.repo),
                "resolve-tab",
                "--token",
                claim["token"],
                "--bridge-thread-id",
                "cli-resolver-thread",
                "--expected-remote-project-id",
                "g-p-demo",
                "--pages-json",
                "[]",
                "--owners-json",
                "[]",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        self.assertEqual(json.loads(opened.stdout)["action"], "open-canonical-tab")

        page_url = "https://chatgpt.com/g/g-p-demo-title/c/cli-created"
        recovery = Path(__file__).resolve().parents[1] / "scripts" / "check_browser_recovery.py"
        recovered = subprocess.run(
            [
                sys.executable,
                str(recovery),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "cli-resolver-thread",
                "--browser-lease-token",
                claim["token"],
                "--expected-project-id",
                "g-p-demo",
                "--conversation-bootstrap",
                "--pages-json",
                json.dumps([{"page_id": 51, "url": page_url}]),
                "--owners-json",
                json.dumps([{"page_id": 51, "owner_token": claim["tab_owner_token"]}]),
                "--observed-page-url",
                page_url,
                "--matching-page-count",
                "1",
                "--observed-page-id",
                "51",
                "--snapshot-page-id",
                "51",
                "--observed-tab-owner-token",
                claim["tab_owner_token"],
                "--pre-submit-boundary",
                "new-conversation",
                "--expected-prompt-sha256",
                "a" * 64,
                "--composer-state",
                "submitted",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        recovery_output = json.loads(recovered.stdout)
        self.assertEqual(
            recovery_output["action"],
            "promote-bootstrap-do-not-resend",
        )
        self.assertFalse(recovery_output["navigation_authorized"])
        self.assertEqual(
            recovery_output["page_transition_evidence"],
            "current-same-page-observation-only",
        )

    def test_preflight_rejects_spoken_count_that_disagrees_with_pages(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="count-worker",
            thread_id="count-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        script = Path(__file__).resolve().parents[1] / "scripts" / "check_browser_preflight.py"
        page_url = "https://chatgpt.com/g/g-p-demo/project"
        result = subprocess.run(
            [
                sys.executable,
                str(script),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "count-thread",
                "--browser-lease-token",
                claim["token"],
                "--conversation-bootstrap",
                "--requested-model",
                "最新",
                "--selected-ui-label",
                "最新",
                "--model-selection-kind",
                "latest-alias",
                "--requested-thinking-intensity",
                "6 Pro",
                "--selected-thinking-intensity",
                "6 Pro",
                "--mcp-host-os",
                "windows",
                "--browser-host-os",
                "windows",
                "--mcp-temp-root",
                str(staging_windows_root()),
                "--expected-project-id",
                "g-p-demo",
                "--observed-project-id",
                "g-p-demo",
                "--expected-workspace",
                "workspace-a",
                "--observed-workspace",
                "workspace-a",
                "--expected-account-label",
                "account-a",
                "--observed-account-label",
                "account-a",
                "--binding-status",
                "active",
                "--observed-page-url",
                page_url,
                "--pages-json",
                json.dumps([{"page_id": 61, "url": page_url}]),
                "--owners-json",
                json.dumps([{"pageId": 61, "owner": claim["tab_owner_token"]}]),
                "--matching-page-count",
                "0",
                "--observed-page-id",
                "61",
                "--snapshot-page-id",
                "61",
                "--observed-tab-owner-token",
                claim["tab_owner_token"],
                "--pre-submit-boundary",
                "new-conversation",
                "--prompt-sha256",
                "b" * 64,
            ],
            check=False,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        self.assertNotEqual(result.returncode, 0)
        self.assertIn("disagrees", result.stderr)


if __name__ == "__main__":
    unittest.main()
