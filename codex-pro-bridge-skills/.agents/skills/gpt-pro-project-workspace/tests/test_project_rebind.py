from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import unittest
from pathlib import Path


SHARED = Path(__file__).resolve().parents[2] / ".shared"
sys.path.insert(0, str(SHARED))

from bridge_store import BridgeError  # noqa: E402
from project_store import BridgeProjectStore, PROJECT_SCHEMA_VERSION  # noqa: E402
from source_manifest import ProjectSourceManager  # noqa: E402


REMOTE_A = "g-p-" + "a" * 32
REMOTE_B = "g-p-" + "b" * 32
REMOTE_C = "g-p-" + "c" * 32


class ProjectRebindTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp = tempfile.TemporaryDirectory()
        self.repo = Path(self.temp.name)
        self.store = BridgeProjectStore(self.repo)
        self.project_id = "bridge-project"
        self.store.create_project(self.project_id, title="Bridge Project")
        self._bind(REMOTE_A)
        self.source_path = self.repo / "source.md"
        self.source_path.write_text("source A\n", encoding="utf-8")
        self.old_manifest_bytes = self._write_old_manifest()

    def tearDown(self) -> None:
        self.temp.cleanup()

    def _url(self, remote_id: str) -> str:
        return f"https://chatgpt.com/g/{remote_id}-project"

    def _bind(self, remote_id: str, *, allow_rebind: bool = False):
        return self.store.bind_remote(
            self.project_id,
            remote_url=self._url(remote_id),
            workspace="workspace",
            account_label="account",
            verified=True,
            allow_rebind=allow_rebind,
        )

    def _write_old_manifest(self) -> bytes:
        manifest = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "bridge_project_id": self.project_id,
            "remote_project_id": REMOTE_A,
            "updated_at": "2026-09-14T01:00:00+00:00",
            "last_inventory_checked_at": "2026-09-14T01:00:00+00:00",
            "last_inventory_verified_at": "2026-09-14T01:00:00+00:00",
            "sources": [
                {
                    "source_id": "old-source",
                    "role": "reference",
                    "local_path": "source.md",
                    "remote_name": "bridge--reference-source--old.md",
                    "sha256": hashlib.sha256(b"source A\n").hexdigest(),
                    "size_bytes": len(b"source A\n"),
                    "ownership": "bridge_managed",
                    "sync_status": "synced",
                }
            ],
            "remote_inventory": [
                {
                    "name": "bridge--reference-source--old.md",
                    "ownership": "bridge_managed",
                    "size_bytes": len(b"source A\n"),
                }
            ],
        }
        # Deliberate CRLF and non-canonical spacing prove the archive is byte-exact.
        raw = (json.dumps(manifest, indent=1) + "\n").replace("\n", "\r\n").encode()
        path = self.store.source_manifest_path(self.project_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(raw)
        return raw

    def _rebind(self):
        return self._bind(REMOTE_B, allow_rebind=True)

    def test_rebind_archives_exact_manifest_and_starts_unverified_inventory(self) -> None:
        self._rebind()

        manifest = json.loads(
            self.store.source_manifest_path(self.project_id).read_text(encoding="utf-8")
        )
        self.assertEqual(manifest["remote_project_id"], REMOTE_B)
        self.assertEqual(manifest["inventory_state"], "unverified")
        self.assertEqual(manifest["sources"], [])
        self.assertEqual(manifest["remote_inventory"], [])
        self.assertFalse(self.store.verify(self.project_id)["inventory_verified"])

        archive = self.repo / manifest["rebind_transition"]["archived_manifest"]
        self.assertEqual(archive.read_bytes(), self.old_manifest_bytes)
        self.assertEqual(
            manifest["rebind_transition"]["archived_manifest_sha256"],
            hashlib.sha256(self.old_manifest_bytes).hexdigest(),
        )

    def test_same_remote_binding_preserves_manifest_verbatim(self) -> None:
        before = self.store.source_manifest_path(self.project_id).read_bytes()
        self._bind(REMOTE_A)
        self.assertEqual(
            self.store.source_manifest_path(self.project_id).read_bytes(), before
        )
        self.assertFalse(self.store.source_manifest_archive_dir(self.project_id).exists())

    def test_same_remote_legacy_empty_manifest_remains_eligible(self) -> None:
        legacy = {
            "schema_version": PROJECT_SCHEMA_VERSION,
            "bridge_project_id": self.project_id,
            "remote_project_id": REMOTE_A,
            "updated_at": "2026-09-14T01:00:00+00:00",
            "sources": [],
            "remote_inventory": [],
        }
        path = self.store.source_manifest_path(self.project_id)
        path.write_text(json.dumps(legacy), encoding="utf-8")
        before = path.read_bytes()
        self._bind(REMOTE_A)
        self.assertEqual(path.read_bytes(), before)
        self.assertTrue(self.store.verify(self.project_id)["inventory_verified"])

    def test_repeat_rebind_is_idempotent_and_does_not_reuse_old_sources(self) -> None:
        self._rebind()
        first = self.store.source_manifest_path(self.project_id).read_bytes()
        self._rebind()
        self.assertEqual(self.store.source_manifest_path(self.project_id).read_bytes(), first)
        self.assertEqual(
            len(list(self.store.source_manifest_archive_dir(self.project_id).glob("*.json"))),
            1,
        )

        manager = ProjectSourceManager(self.repo)
        with self.assertRaisesRegex(BridgeError, "have not been inventoried"):
            manager.plan(self.project_id)
        plan, _ = manager.plan(
            self.project_id,
            remote_inventory=[
                {
                    "name": "bridge--reference-source--old.md",
                    "ownership": "bridge_managed",
                }
            ],
        )
        self.assertEqual(plan["desired_sources"], [])
        self.assertEqual(plan["reuse"], [])
        self.assertEqual(plan["uploads"], [])

    def test_partial_rebind_is_rejected_by_source_operations_then_resumes(self) -> None:
        manager = ProjectSourceManager(self.repo)
        plan, plan_path = manager.plan(
            self.project_id,
            remote_inventory=[
                {
                    "name": "bridge--reference-source--old.md",
                    "ownership": "bridge_managed",
                }
            ],
        )
        self.store._prepare_source_manifest_rebind(
            self.project_id,
            previous_remote_project_id=REMOTE_A,
            next_remote_project_id=REMOTE_B,
            occurred_at="2026-09-14T02:00:00+00:00",
        )
        with self.assertRaisesRegex(BridgeError, "different ChatGPT Project binding"):
            ProjectSourceManager(self.repo).load_manifest(self.project_id)
        with self.assertRaisesRegex(BridgeError, "different ChatGPT Project binding"):
            manager.plan(self.project_id, remote_inventory=[])
        with self.assertRaisesRegex(BridgeError, "different ChatGPT Project binding"):
            manager.record(
                self.project_id,
                plan_path=plan_path,
                observation={
                    "present": [item["remote_name"] for item in plan["desired_sources"]],
                    "remote_inventory": plan["remote_inventory"],
                },
            )
        with self.assertRaisesRegex(BridgeError, "different ChatGPT Project binding"):
            self.store.verify(self.project_id)

        pending = json.loads(
            self.store.source_manifest_path(self.project_id).read_text(encoding="utf-8")
        )
        archive = self.repo / pending["rebind_transition"]["archived_manifest"]
        self.assertEqual(archive.read_bytes(), self.old_manifest_bytes)
        self._rebind()
        self.assertEqual(self.store.load_binding(self.project_id)["remote_project_id"], REMOTE_B)
        self.assertEqual(
            ProjectSourceManager(self.repo).load_manifest(self.project_id)["remote_project_id"],
            REMOTE_B,
        )
        self.assertEqual(archive.read_bytes(), self.old_manifest_bytes)

    def test_invalid_preexisting_manifest_fails_before_mutation(self) -> None:
        path = self.store.source_manifest_path(self.project_id)
        invalid = json.loads(path.read_text(encoding="utf-8"))
        invalid["remote_project_id"] = REMOTE_C
        raw = json.dumps(invalid, indent=2).encode()
        path.write_bytes(raw)
        binding_before = (
            self.store.project_dir(self.project_id) / "remote-binding.json"
        ).read_bytes()

        with self.assertRaisesRegex(BridgeError, "neither the current nor requested"):
            self._rebind()
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(
            (self.store.project_dir(self.project_id) / "remote-binding.json").read_bytes(),
            binding_before,
        )
        self.assertFalse(self.store.source_manifest_archive_dir(self.project_id).exists())

    def test_present_falsey_json_manifest_is_never_treated_as_absent(self) -> None:
        path = self.store.source_manifest_path(self.project_id)
        binding_path = self.store.project_dir(self.project_id) / "remote-binding.json"
        binding_before = binding_path.read_bytes()

        for raw in (b"[]", b"null", b"{}"):
            with self.subTest(raw=raw):
                path.write_bytes(raw)
                with self.assertRaisesRegex(BridgeError, "manifest identity is invalid"):
                    self._rebind()
                self.assertEqual(path.read_bytes(), raw)
                self.assertEqual(binding_path.read_bytes(), binding_before)
                self.assertFalse(
                    self.store.source_manifest_archive_dir(self.project_id).exists()
                )

    def test_present_manifest_without_remote_identity_is_rejected(self) -> None:
        path = self.store.source_manifest_path(self.project_id)
        raw = json.dumps({"schema_version": PROJECT_SCHEMA_VERSION, "bridge_project_id": self.project_id,
                          "sources": []}).encode()
        path.write_bytes(raw)
        binding_path = self.store.project_dir(self.project_id) / "remote-binding.json"
        binding_before = binding_path.read_bytes()
        with self.assertRaisesRegex(BridgeError, "neither the current nor requested"):
            self._rebind()
        self.assertEqual(path.read_bytes(), raw)
        self.assertEqual(binding_path.read_bytes(), binding_before)
        self.assertFalse(self.store.source_manifest_archive_dir(self.project_id).exists())

    def test_observed_new_inventory_verifies_rebound_project(self) -> None:
        self._rebind()
        result = ProjectSourceManager(self.repo).reconcile_remote_inventory(
            self.project_id,
            remote_inventory=[{"name": "user-paper.pdf", "ownership": "user_managed"}],
        )
        self.assertTrue(result["valid"])
        manifest = ProjectSourceManager(self.repo).load_manifest(self.project_id)
        self.assertEqual(manifest["inventory_state"], "verified")
        self.assertEqual(manifest["remote_inventory"][0]["name"], "user-paper.pdf")
        self.assertTrue(self.store.verify(self.project_id)["inventory_verified"])


if __name__ == "__main__":
    unittest.main()
