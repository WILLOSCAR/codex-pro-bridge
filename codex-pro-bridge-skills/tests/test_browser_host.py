import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path, PureWindowsPath
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
SHARED = ROOT / ".agents" / "skills" / ".shared"
SCRIPTS = ROOT / ".agents" / "skills" / "gpt-pro-question-window" / "scripts"
sys.path.insert(0, str(SHARED))

from bridge_store import BridgeError, file_lock  # noqa: E402
from browser_host import (  # noqa: E402
    cleanup_staged_file,
    stage_browser_file,
    verify_staged_file,
)
from host_config import load_browser_host_config  # noqa: E402


class BrowserHostTest(unittest.TestCase):
    def _wsl_environment(self, root: Path):
        execution_root = root / "windows-backed" / "staging"
        config_path = root / "browser-host.json"
        config_path.write_text(
            json.dumps(
                {
                    "schema_version": 1,
                    "topology": "wsl-windows",
                    "staging": {
                        "execution_root_env": "TEST_EXECUTION_ROOT",
                        "browser_root_env": "TEST_BROWSER_ROOT",
                        "lock_path_env": "TEST_LOCK_PATH",
                    },
                }
            ),
            encoding="utf-8",
        )
        return {
            "CODEX_PRO_BRIDGE_HOST_CONFIG": str(config_path),
            "TEST_EXECUTION_ROOT": str(execution_root),
            "TEST_BROWSER_ROOT": r"C:\CodexBridge\staging",
            "TEST_LOCK_PATH": str(root / "locks" / "staging.lock"),
        }

    def test_wsl_windows_stage_verify_and_exact_cleanup(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "evidence bundle.zip"
            source.write_bytes(b"bridge-evidence")
            expected_digest = hashlib.sha256(source.read_bytes()).hexdigest()
            with patch.dict(os.environ, self._wsl_environment(root), clear=False):
                staged = stage_browser_file(source, thread_id="thread/unsafe value")
                staged_path = Path(staged["staged_execution_path"])
                self.assertTrue(staged_path.is_file())
                self.assertEqual(staged["source_sha256"], expected_digest)
                self.assertEqual(staged["topology"], "wsl-windows")
                self.assertEqual(
                    PureWindowsPath(staged["staged_browser_path"]).parent,
                    PureWindowsPath(r"C:\CodexBridge\staging"),
                )

                verified = verify_staged_file(
                    staged_path,
                    source_path=source,
                    expected_sha256=expected_digest,
                    expected_browser_path=staged["staged_browser_path"],
                )
                self.assertTrue(verified["verified"])
                self.assertEqual(verified["staged_sha256"], expected_digest)

                with self.assertRaises(BridgeError):
                    cleanup_staged_file(staged_path, expected_sha256="0" * 64)
                self.assertTrue(staged_path.exists())
                cleaned = cleanup_staged_file(
                    staged_path, expected_sha256=expected_digest
                )
                self.assertTrue(cleaned["cleaned"])
                self.assertFalse(staged_path.exists())

    def test_staging_cli_accepts_digest_verified_mapping(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "bundle.zip"
            source.write_bytes(b"preflight-evidence")
            environment = self._wsl_environment(root)
            with patch.dict(os.environ, environment, clear=False):
                staged = stage_browser_file(source, thread_id="thread-1")
                command = [
                    sys.executable,
                    str(SCRIPTS / "manage_browser_staging.py"),
                    "verify",
                    "--source",
                    str(source),
                    "--staged-path",
                    staged["staged_execution_path"],
                    "--expected-browser-path",
                    staged["staged_browser_path"],
                ]
                result = subprocess.run(
                    command,
                    check=True,
                    capture_output=True,
                    text=True,
                    env=os.environ.copy(),
                )
                observed = json.loads(result.stdout)
                self.assertTrue(observed["verified"])
                self.assertEqual(observed["topology"], "wsl-windows")
                self.assertEqual(
                    observed["staged_browser_path"], staged["staged_browser_path"]
                )
                cleanup_staged_file(
                    staged["staged_execution_path"],
                    expected_sha256=staged["source_sha256"],
                )

    def test_non_windows_requires_an_explicit_mapping(self):
        if os.name == "nt":
            self.skipTest("Native Windows has a safe default mapping")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(BridgeError, "CODEX_PRO_BRIDGE_HOST_CONFIG"):
                load_browser_host_config()

    def test_config_rejects_relative_browser_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            environment = self._wsl_environment(root)
            environment["TEST_BROWSER_ROOT"] = "relative\\staging"
            with patch.dict(os.environ, environment, clear=False):
                with self.assertRaisesRegex(BridgeError, "absolute Windows path"):
                    load_browser_host_config()

    def test_config_rejects_relative_execution_root(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            environment = self._wsl_environment(root)
            environment["TEST_EXECUTION_ROOT"] = "relative/staging"
            with patch.dict(os.environ, environment, clear=False):
                with self.assertRaisesRegex(BridgeError, "absolute path"):
                    load_browser_host_config()

    def test_stage_rejects_source_symlink(self):
        if not hasattr(os, "symlink"):
            self.skipTest("Symlinks are unavailable")
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.zip"
            source.write_bytes(b"source")
            link = root / "link.zip"
            try:
                link.symlink_to(source)
            except OSError as exc:
                self.skipTest(f"Symlink creation is unavailable: {exc}")
            with patch.dict(os.environ, self._wsl_environment(root), clear=False):
                with self.assertRaisesRegex(BridgeError, "must not be a symlink"):
                    stage_browser_file(link, thread_id="thread-1")

    def test_file_lock_is_cross_platform_callable(self):
        with tempfile.TemporaryDirectory() as temp:
            lock_path = Path(temp) / "nested" / "bridge.lock"
            with file_lock(lock_path):
                self.assertTrue(lock_path.is_file())

    @unittest.skipUnless(os.name == "nt", "Windows-native default only")
    def test_windows_native_default_stays_inside_workspace(self):
        with tempfile.TemporaryDirectory() as temp, patch.dict(
            os.environ, {"CODEX_PRO_BRIDGE_HOST_CONFIG": ""}, clear=False
        ), patch("host_config.Path.cwd", return_value=Path(temp)):
            config = load_browser_host_config()
            self.assertEqual(config.topology, "windows-native")
            self.assertEqual(
                config.execution_root,
                Path(temp).resolve()
                / ".codex"
                / "codex-pro-bridge"
                / "browser-staging",
            )


if __name__ == "__main__":
    unittest.main()
