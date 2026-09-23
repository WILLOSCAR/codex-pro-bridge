"""Real local processes, fake browser MCP: persistent authorization transport."""
import json
from pathlib import Path
import subprocess
import sys
import tempfile
import time
import unittest

SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
sys.path[:0] = [str(SCRIPTS), str(SCRIPTS.parents[1] / ".shared")]
from bridge_runtime.connection_service import SharedClient
from bridge_runtime.rpc import RpcError

BACKEND = r'''
import json, os, sys, time
from pathlib import Path
root = Path(sys.argv[1])
for line in sys.stdin:
    m = json.loads(line)
    if 'id' not in m: continue
    if m['method'] == 'initialize':
        with (root/'starts').open('a') as f: f.write(str(os.getpid())+'\n')
        result = {'protocolVersion':'2024-11-05','capabilities':{},'serverInfo':{'name':'test','version':'1'}}
    elif m['method'] == 'tools/list':
        result = {'tools':[{'name':'list_pages','inputSchema':{'type':'object','properties':{}}},
                           {'name':'stall','inputSchema':{'type':'object','properties':{'pageId':{}}}}]}
    else:
        while not (root/'authorized').exists(): time.sleep(.01)
        if m.get('params',{}).get('name') == 'stall': time.sleep(3)
        result = {'content':[{'type':'text','text':'## Pages\n1: test (https://chatgpt.com/)'}]}
    print(json.dumps({'jsonrpc':'2.0','id':m['id'],'result':result}), flush=True)
'''


class ConnectionServiceTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.backend = self.root / "fake.py"
        self.backend.write_text(BACKEND)
        self.config = {"state_dir":str(self.root / "private"),
            "browser_command":[sys.executable, str(self.backend), str(self.root)],
            "browser_connect_timeout_seconds":1, "browser_tool_timeout_seconds":2}
        self.client = None

    def tearDown(self):
        if self.client:
            self.client.shutdown()
            deadline = time.monotonic() + 5
            while self.client._attach() and time.monotonic() < deadline:
                time.sleep(.05)
        self.temp.cleanup()

    def test_independent_workers_reuse_one_backend(self):
        (self.root / "authorized").touch()
        self.client = SharedClient(self.config)
        pid = self.client.status()["pid"]
        self.client.call("list_pages")
        self.client.close()
        config_file = self.root / "config.json"
        config_file.write_text(json.dumps(self.config))
        code = "import json,sys; from pathlib import Path; sys.path[:0]=sys.argv[2:]; from bridge_runtime.connection_service import SharedClient; c=SharedClient(json.loads(Path(sys.argv[1]).read_text())); c.call('list_pages'); print(c.status()['pid']); c.close()"
        second = subprocess.run([sys.executable,"-c",code,str(config_file),str(SCRIPTS),str(SCRIPTS.parents[1]/'.shared')],
                                capture_output=True,text=True,timeout=15)
        self.assertEqual(second.returncode,0,second.stderr)
        self.assertEqual(int(second.stdout.strip()),pid)
        self.assertEqual(len((self.root / "starts").read_text().splitlines()),1)

    def test_late_authorization_survives_callers_timeout(self):
        self.client = SharedClient(self.config)
        pid = self.client.status()["pid"]
        with self.assertRaises(RpcError):
            self.client._request("call", timeout=.1, name="list_pages", arguments={})
        time.sleep(1.1)  # longer than this worker's configured connection budget
        self.assertEqual(self.client.status()["phase"],"connecting-browser")
        (self.root / "authorized").touch()
        deadline = time.monotonic() + 5
        while self.client.status()["phase"] != "connected" and time.monotonic() < deadline:
            time.sleep(.02)
        second = SharedClient(self.config)
        self.assertEqual(second.status()["pid"],pid)
        second.call("list_pages")
        self.assertEqual(len((self.root / "starts").read_text().splitlines()),1)

    def test_wrong_token_cannot_use_service(self):
        self.client = SharedClient(self.config)
        original = self.client.endpoint["token"]
        try:
            self.client.endpoint["token"] = "wrong"
            with self.assertRaises(RpcError):
                self.client.status()
        finally:
            self.client.endpoint["token"] = original
        self.assertEqual(self.client.status()["phase"],"ready-for-connection")

    def test_tool_error_does_not_discard_a_healthy_connection(self):
        (self.root / "authorized").touch()
        self.client = SharedClient(self.config)
        self.client.call("list_pages")
        with self.assertRaises(RpcError):
            self.client.call("unknown-tool")
        self.assertEqual(self.client.status()["phase"],"connected")
        self.client.call("list_pages")

    def test_unknown_transport_outcome_blocks_further_calls(self):
        (self.root / "authorized").touch()
        self.client = SharedClient(self.config)
        self.client.call("list_pages")
        with self.assertRaises(RpcError):
            self.client.call("stall", pageId=1)
        self.assertEqual(self.client.status()["phase"],"connection-error")
        with self.assertRaisesRegex(RpcError, "no automatic reconnect"):
            self.client.call("list_pages")


if __name__ == "__main__":
    unittest.main()
