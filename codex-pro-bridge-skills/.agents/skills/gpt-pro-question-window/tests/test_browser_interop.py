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
    release_browser_lease,
)
from browser_host import staging_windows_root
from browser_identity import (
    bootstrap_destination_from_url,
    resolve_owned_tab,
    verify_bootstrap_tab,
    verify_claimed_tab,
)
from browser_observations import (
    normalize_owners_observation,
    normalize_pages_observation,
)


class BrowserInteropTest(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name).resolve() / "repo"
        self.repo.mkdir()
        self.previous_state_dir = os.environ.get("CODEX_PRO_BRIDGE_BROWSER_STATE_DIR")
        os.environ["CODEX_PRO_BRIDGE_BROWSER_STATE_DIR"] = str(
            Path(self.temp.name).resolve() / "browser-state"
        )
        self.script = Path(__file__).resolve().parents[1] / "scripts" / "manage_browser_lease.py"

    def tearDown(self) -> None:
        if self.previous_state_dir is None:
            os.environ.pop("CODEX_PRO_BRIDGE_BROWSER_STATE_DIR", None)
        else:
            os.environ["CODEX_PRO_BRIDGE_BROWSER_STATE_DIR"] = self.previous_state_dir
        self.temp.cleanup()

    def test_raw_mcp_pages_and_owner_aliases_normalize_without_dropping_pages(self) -> None:
        raw_pages = {
            "content": [
                {
                    "type": "text",
                    "text": (
                        "## Pages\n"
                        "1: Search (https://example.com/path?q=1) [selected]\n"
                        "2: ChatGPT (https://chatgpt.com/g/g-p-demo/project?tab=chats)\n"
                        "3: DevTools (chrome://inspect/#remote-debugging)\n"
                        "4: Blank (about:blank)\n"
                        "5: Local (file:///C:/tmp/review.html)"
                    ),
                }
            ]
        }
        pages = normalize_pages_observation(raw_pages)
        self.assertEqual(
            [page["page_id"] for page in pages], ["1", "2", "3", "4", "5"]
        )
        self.assertEqual(pages[0]["url"], "https://example.com/path?q=1")
        self.assertEqual(pages[2]["url"], "chrome://inspect/#remote-debugging")
        self.assertEqual(pages[3]["url"], "about:blank")
        self.assertEqual(pages[4]["url"], "file:///C:/tmp/review.html")
        self.assertEqual(
            normalize_pages_observation([{"pageId": 3, "url": "https://chatgpt.com/"}])[0][
                "page_id"
            ],
            3,
        )
        owner = normalize_owners_observation(
            [
                {
                    "pageId": 2,
                    "observation": {
                        "content": [
                            {
                                "type": "text",
                                "text": (
                                    "Script ran on page and returned:\n"
                                    "```json\n{\"url\":\"https://chatgpt.com/\","
                                    "\"owner\":\"owner-a\"}\n```"
                                ),
                            }
                        ]
                    },
                }
            ]
        )
        self.assertEqual(owner, [{"page_id": 2, "owner_token": "owner-a"}])
        unowned = normalize_owners_observation(
            [
                {
                    "pageId": 3,
                    "content": [
                        {
                            "type": "text",
                            "text": "```json\n{\"owner\":null}\n```",
                        }
                    ],
                }
            ]
        )
        self.assertEqual(unowned, [{"page_id": 3, "owner_token": ""}])

    def test_conflicting_aliases_are_rejected(self) -> None:
        with self.assertRaisesRegex(BridgeError, "Conflicting page id aliases"):
            normalize_pages_observation(
                [{"page_id": 1, "pageId": 2, "url": "https://chatgpt.com/"}]
            )
        with self.assertRaisesRegex(BridgeError, "Conflicting owner token aliases"):
            normalize_owners_observation(
                [{"page_id": 1, "owner": "one", "tab_owner_token": "two"}]
            )
        with self.assertRaisesRegex(BridgeError, "both observation and content"):
            normalize_owners_observation(
                [
                    {
                        "pageId": 1,
                        "observation": {"content": []},
                        "content": [],
                    }
                ]
            )
        with self.assertRaisesRegex(BridgeError, "reports an error"):
            normalize_pages_observation(
                {"isError": True, "content": [{"type": "text", "text": "## Pages"}]}
            )
        with self.assertRaisesRegex(BridgeError, "reports an error"):
            normalize_owners_observation(
                [
                    {
                        "pageId": 1,
                        "isError": True,
                        "content": [
                            {"type": "text", "text": "```json\n{\"owner\":null}\n```"}
                        ],
                    }
                ]
            )

    def test_project_home_allows_only_tab_chats_query(self) -> None:
        self.assertEqual(
            bootstrap_destination_from_url(
                "https://chatgpt.com/g/g-p-demo-title/project?tab=chats", "g-p-demo"
            ),
            "g-p-demo-title",
        )
        for url in (
            "https://chatgpt.com/g/g-p-demo/project?tab=sources",
            "https://chatgpt.com/g/g-p-demo/project?tab=chats&x=1",
            "https://chatgpt.com/g/g-p-demo/project?tab=%63hats",
            "https://chatgpt.com/g/g-p-demo/project?tab=chats&",
            "https://chatgpt.com/g/g-p-demo/project?tab=chats#top",
        ):
            with self.subTest(url=url), self.assertRaises(BridgeError):
                bootstrap_destination_from_url(url, "g-p-demo")

    def test_multi_url_verifiers_require_resolver_owner_proof(self) -> None:
        conversation = acquire_browser_lease(
            self.repo,
            holder="verify-conversation",
            thread_id="verify-conversation-thread",
            expected_conversation_id="verify-conversation-id",
            scope="conversation",
        )
        with self.assertRaisesRegex(BridgeError, "resolver proof"):
            verify_claimed_tab(
                self.repo,
                claim_token=conversation["token"],
                thread_id="verify-conversation-thread",
                browser_profile=conversation["browser_profile"],
                expected_project_id="",
                expected_conversation_id="verify-conversation-id",
                observed_page_url="https://chatgpt.com/c/verify-conversation-id",
                matching_page_count=2,
                observed_page_id="1",
                snapshot_page_id="1",
                observed_tab_owner_token=conversation["tab_owner_token"],
            )
        release_browser_lease(self.repo, token=conversation["token"])

        bootstrap = acquire_browser_lease(
            self.repo,
            holder="verify-bootstrap",
            thread_id="verify-bootstrap-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        with self.assertRaisesRegex(BridgeError, "resolver proof"):
            verify_bootstrap_tab(
                self.repo,
                claim_token=bootstrap["token"],
                thread_id="verify-bootstrap-thread",
                browser_profile=bootstrap["browser_profile"],
                expected_project_id="g-p-demo",
                observed_page_url="https://chatgpt.com/g/g-p-demo/project",
                matching_page_count=2,
                observed_page_id="2",
                snapshot_page_id="2",
                observed_tab_owner_token=bootstrap["tab_owner_token"],
            )

    def test_multiple_unowned_project_homes_choose_numeric_min_and_preflight(self) -> None:
        claim = acquire_browser_lease(
            self.repo,
            holder="multi-worker",
            thread_id="multi-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        pages = [
            {"pageId": 20, "url": "https://chatgpt.com/g/g-p-demo/project"},
            {"pageId": 4, "url": "https://chatgpt.com/g/g-p-demo-title/project?tab=chats"},
            {"pageId": 2, "url": "https://example.com/"},
        ]
        empty_owners = [
            {"pageId": 20, "owner": ""},
            {"pageId": 4, "owner": ""},
        ]
        selected = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="multi-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=pages,
            owners=empty_owners,
        )
        self.assertEqual(selected["action"], "set-owner-token")
        self.assertEqual(selected["page_id"], "4")
        self.assertEqual(selected["matching_page_count"], 2)
        self.assertEqual(selected["owner_selected_count"], 1)

        owners = [
            {"pageId": 20, "owner": ""},
            {"pageId": 4, "owner": claim["tab_owner_token"]},
        ]
        rebound = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="multi-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=pages,
            owners=owners,
        )
        self.assertEqual(rebound["action"], "reuse-owned-tab")
        self.assertEqual(rebound["matching_page_count"], 2)

        preflight = Path(__file__).resolve().parents[1] / "scripts" / "check_browser_preflight.py"
        result = subprocess.run(
            [
                sys.executable,
                str(preflight),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                "multi-thread",
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
                "workspace",
                "--observed-workspace",
                "workspace",
                "--expected-account-label",
                "account",
                "--observed-account-label",
                "account",
                "--binding-status",
                "active",
                "--observed-page-url",
                pages[1]["url"],
                "--pages-json",
                json.dumps(pages),
                "--owners-json",
                json.dumps(owners),
                "--matching-page-count",
                "2",
                "--observed-page-id",
                "4",
                "--snapshot-page-id",
                "4",
                "--observed-tab-owner-token",
                claim["tab_owner_token"],
                "--pre-submit-boundary",
                "new-conversation",
                "--prompt-sha256",
                "a" * 64,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        output = json.loads(result.stdout)
        self.assertEqual(output["matching_page_count"], 2)
        self.assertEqual(output["owner_selected_count"], 1)

    def test_protected_project_home_is_skipped_when_free_candidate_exists(self) -> None:
        protected = acquire_browser_lease(
            self.repo,
            holder="protected",
            thread_id="protected-thread",
            expected_remote_project_id="g-p-other",
            scope="project",
            bootstrap=True,
        )
        claim = acquire_browser_lease(
            self.repo,
            holder="current",
            thread_id="current-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        result = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="current-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="g-p-demo",
            pages=[
                {"page_id": 3, "url": "https://chatgpt.com/g/g-p-demo/project"},
                {"page_id": 8, "url": "https://chatgpt.com/g/g-p-demo/project?tab=chats"},
            ],
            owners=[
                {"page_id": 3, "owner_token": protected["tab_owner_token"]},
                {"page_id": 8, "owner_token": ""},
            ],
        )
        self.assertEqual(result["action"], "set-owner-token")
        self.assertEqual(result["page_id"], "8")
        self.assertEqual(result["matching_page_count"], 2)

    def test_bound_conversation_ignores_unowned_duplicate_in_preflight_and_recovery(self) -> None:
        conversation_id = "bound-conversation"
        claim = acquire_browser_lease(
            self.repo,
            holder="bound-worker",
            thread_id="bound-thread",
            expected_conversation_id=conversation_id,
            scope="conversation",
        )
        url = f"https://chatgpt.com/c/{conversation_id}"
        pages = [
            {"pageId": 5, "url": url},
            {"pageId": 9, "url": url},
        ]
        owners = [
            {"pageId": 5, "owner": claim["tab_owner_token"]},
            {"pageId": 9, "owner": ""},
        ]
        resolved = resolve_owned_tab(
            self.repo,
            claim_token=claim["token"],
            thread_id="bound-thread",
            browser_profile=claim["browser_profile"],
            expected_project_id="",
            pages=pages,
            owners=owners,
        )
        self.assertEqual(resolved["action"], "reuse-owned-tab")
        self.assertEqual(resolved["page_id"], "5")
        self.assertEqual(resolved["matching_page_count"], 2)
        self.assertEqual(resolved["owner_selected_count"], 1)

        common = [
            "--repo",
            str(self.repo),
            "--bridge-thread-id",
            "bound-thread",
            "--browser-lease-token",
            claim["token"],
            "--expected-conversation-id",
            conversation_id,
            "--observed-page-url",
            url,
            "--matching-page-count",
            "2",
            "--pages-json",
            json.dumps(pages),
            "--owners-json",
            json.dumps(owners),
            "--observed-page-id",
            "5",
            "--snapshot-page-id",
            "5",
            "--observed-tab-owner-token",
            claim["tab_owner_token"],
            "--pre-submit-boundary",
            "turn-before",
        ]
        preflight = Path(__file__).resolve().parents[1] / "scripts" / "check_browser_preflight.py"
        checked = subprocess.run(
            [
                sys.executable,
                str(preflight),
                *common,
                "--observed-conversation-id",
                conversation_id,
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
                "--prompt-sha256",
                "b" * 64,
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        self.assertEqual(json.loads(checked.stdout)["owner_selected_count"], 1)

        recovery = Path(__file__).resolve().parents[1] / "scripts" / "check_browser_recovery.py"
        recovered = subprocess.run(
            [
                sys.executable,
                str(recovery),
                *common,
                "--expected-prompt-sha256",
                "b" * 64,
                "--composer-state",
                "empty",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        recovery_output = json.loads(recovered.stdout)
        self.assertEqual(recovery_output["action"], "enter-prompt-and-run-preflight")
        self.assertEqual(recovery_output["matching_page_count"], 2)
        self.assertEqual(recovery_output["owner_selected_count"], 1)
        self.assertFalse(recovery_output["navigation_authorized"])
        self.assertEqual(
            recovery_output["page_transition_evidence"],
            "current-same-page-observation-only",
        )

        mismatched = list(common)
        mismatched[mismatched.index(url)] = "https://chatgpt.com/"
        conflict = subprocess.run(
            [
                sys.executable,
                str(recovery),
                *mismatched,
                "--expected-prompt-sha256",
                "b" * 64,
                "--composer-state",
                "empty",
            ],
            capture_output=True,
            text=True,
            check=False,
            env=os.environ.copy(),
        )
        self.assertNotEqual(conflict.returncode, 0)
        self.assertIn("not evidence of page navigation", conflict.stderr)
        self.assertIn("HOLD without navigating", conflict.stderr)

    def test_release_without_staging_supports_captured_and_rejects_bootstrap(self) -> None:
        conversation = subprocess.run(
            [
                sys.executable,
                str(self.script),
                "--repo",
                str(self.repo),
                "acquire",
                "--holder",
                "capture-worker",
                "--bridge-thread-id",
                "capture-thread",
                "--expected-conversation-id",
                "capture-conversation",
                "--scope",
                "conversation",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        claim = json.loads(conversation.stdout)
        self.assertEqual(
            claim["release_argv"],
            ["release", "--token", claim["token"]],
        )
        released = subprocess.run(
            [
                sys.executable,
                str(self.script),
                "--repo",
                str(self.repo),
                "release",
                "--token",
                claim["token"],
                "--terminal-state",
                "captured",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        self.assertEqual(json.loads(released.stdout)["terminal_state"], "captured")
        self.assertFalse(json.loads(released.stdout)["staging_cleanup"]["cleaned"])

        bootstrap = acquire_browser_lease(
            self.repo,
            holder="bootstrap-release",
            thread_id="bootstrap-release-thread",
            expected_remote_project_id="g-p-demo",
            scope="project",
            bootstrap=True,
        )
        for terminal in ("captured", "send-accepted"):
            refused = subprocess.run(
                [
                    sys.executable,
                    str(self.script),
                    "--repo",
                    str(self.repo),
                    "release",
                    "--token",
                    bootstrap["token"],
                    "--terminal-state",
                    terminal,
                    "--bootstrap-tab-cleanup",
                    "owner-token-never-bound",
                ],
                check=False,
                capture_output=True,
                text=True,
                env=os.environ.copy(),
            )
            self.assertNotEqual(refused.returncode, 0)

    def test_browser_fallback_save_requires_and_uses_multi_url_owner_proof(self) -> None:
        thread = "capture-multi-thread"
        conversation_id = "capture-multi-conversation"
        url = f"https://chatgpt.com/c/{conversation_id}"
        notes = (
            SHARED_DIR.parent
            / "bundle-algorithm-context"
            / "scripts"
            / "prepare_codex_session_notes.py"
        )
        prepared = subprocess.run(
            [
                sys.executable,
                str(notes),
                "--repo",
                str(self.repo),
                "--bridge-thread-id",
                thread,
                "--goal",
                "capture identity fixture",
                "--gpt-pro-question",
                "question",
                "--summary",
                "fixture",
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        self.assertTrue(prepared.stdout.strip())
        claim = acquire_browser_lease(
            self.repo,
            holder="capture-multi-worker",
            thread_id=thread,
            expected_conversation_id=conversation_id,
            scope="conversation",
        )
        pages = [
            {"pageId": 5, "url": url},
            {"pageId": 9, "url": url},
        ]
        owners = [
            {"pageId": 5, "owner": claim["tab_owner_token"]},
            {"pageId": 9, "owner": ""},
        ]
        save = Path(__file__).resolve().parents[1] / "scripts" / "save_bridge_turn.py"
        base = [
            sys.executable,
            str(save),
            "--repo",
            str(self.repo),
            "--bridge-thread-id",
            thread,
            "--standalone",
            "--web-url",
            url,
            "--expected-conversation-id",
            conversation_id,
            "--browser-lease-token",
            claim["token"],
            "--observed-page-url",
            url,
            "--matching-page-count",
            "2",
            "--observed-page-id",
            "5",
            "--snapshot-page-id",
            "5",
            "--observed-tab-owner-token",
            claim["tab_owner_token"],
            "--capture-route",
            "browser-fallback",
            "--answer-format",
            "copied-markdown",
            "--remote-turn-id",
            "captured-turn",
            "--prompt",
            "question",
            "--answer",
            "answer",
        ]
        missing = subprocess.run(
            base,
            check=False,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        self.assertNotEqual(missing.returncode, 0)
        self.assertIn("resolver proof", missing.stderr)

        duplicate_owners = [
            {"pageId": 5, "owner": claim["tab_owner_token"]},
            {"pageId": 9, "owner": claim["tab_owner_token"]},
        ]
        duplicate = subprocess.run(
            [
                *base,
                "--pages-json",
                json.dumps(pages),
                "--owners-json",
                json.dumps(duplicate_owners),
            ],
            check=False,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        self.assertNotEqual(duplicate.returncode, 0)
        self.assertIn("duplicate-owner", duplicate.stderr)

        saved = subprocess.run(
            [
                *base,
                "--pages-json",
                json.dumps(pages),
                "--owners-json",
                json.dumps(owners),
            ],
            check=True,
            capture_output=True,
            text=True,
            env=os.environ.copy(),
        )
        self.assertTrue(Path(saved.stdout.strip()).is_file())


if __name__ == "__main__":
    unittest.main()
