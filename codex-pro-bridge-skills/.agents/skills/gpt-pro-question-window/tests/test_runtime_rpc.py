import subprocess
import sys
from pathlib import Path
import unittest
from unittest.mock import Mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))
from bridge_runtime.rpc import Client


class ShutdownTest(unittest.TestCase):
    def test_only_initial_read_uses_connection_budget(self):
        client = Client.__new__(Client)
        client.schemas = {"list_pages": {}, "click": {"properties": {"pageId": {}}}}
        client.timeout, client.connect_timeout = 90, 300
        client.browser_connected = False
        client.request = Mock(return_value={"content": []})
        client.call("list_pages")
        client.call("list_pages")
        client.call("click", pageId=1, uid="x")
        self.assertEqual([c.kwargs["timeout"] for c in client.request.call_args_list], [300, 90, 90])

    def test_failed_initial_read_does_not_mark_connected_or_retry(self):
        client = Client.__new__(Client)
        client.schemas = {"list_pages": {}}
        client.timeout, client.connect_timeout = 90, 300
        client.browser_connected = False
        client.request = Mock(return_value={"isError": True, "content": []})
        with self.assertRaises(RuntimeError):
            client.call("list_pages")
        self.assertFalse(client.browser_connected)
        client.request.assert_called_once()

    def test_stdio_eof_runs_server_cleanup(self):
        client = Client.__new__(Client)
        client.process = subprocess.Popen([sys.executable, "-c",
            "import sys; sys.stdin.read(); print('clean-disconnect', flush=True)"],
            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
            text=True, encoding="utf-8")
        try:
            client.close()
            self.assertEqual(client.process.returncode, 0)
            self.assertEqual(client.process.stdout.read().strip(), "clean-disconnect")
            client.close()  # idempotent, including already-closed stdin
        finally:
            if client.process.poll() is None:
                client.process.kill()
                client.process.wait()
            client.process.stdout.close()
            client.process.stderr.close()

    def test_unresponsive_server_still_has_bounded_shutdown(self):
        client = Client.__new__(Client)
        client.process = Mock()
        client.process.poll.return_value = None
        client.process.wait.side_effect = [subprocess.TimeoutExpired("mcp", 5), None]
        client.close()
        client.process.stdin.close.assert_called_once()
        client.process.terminate.assert_called_once()
        client.process.kill.assert_not_called()


if __name__ == "__main__":
    unittest.main()
