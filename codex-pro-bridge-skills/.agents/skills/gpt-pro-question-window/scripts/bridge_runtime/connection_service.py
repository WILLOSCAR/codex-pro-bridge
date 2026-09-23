"""Private loopback transport: keep one authorized MCP alive across workers.

Only transports tool calls. It has no page-selection, Send, retry or task policy.
Workers retain the canonical identity/attempt gates. JSON only, never pickle.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from pathlib import Path
import secrets
import socket
import socketserver
import subprocess
import sys
import threading
import time

from bridge_store import file_lock
from .jobs import read_json, write_json
from .rpc import Client, RpcError, RpcTransportError

MAX_MESSAGE = 32 * 1024 * 1024


def route_config(config):
    return {key: config.get(key, default) for key, default in (
        ("browser_command", []), ("browser_env", {}), ("browser_profile", "chrome-stable-default"),
        ("browser_connect_timeout_seconds", 300), ("browser_tool_timeout_seconds", 90))}


class SharedClient:
    def __init__(self, config):
        self._process = None
        route = route_config(config)
        digest = hashlib.sha256(json.dumps(route, sort_keys=True).encode()).hexdigest()
        self.directory = Path(config["state_dir"]) / "browser-connections" / digest
        self.directory.mkdir(parents=True, exist_ok=True, mode=0o700)
        if os.name != "nt" and self.directory.stat().st_mode & 0o077:
            raise RpcError("Connection service directory must be private (0700)")
        self.timeout = route["browser_connect_timeout_seconds"] + 8 * route["browser_tool_timeout_seconds"] + 10
        with file_lock(self.directory / "start.lock"):
            if not self._attach():
                write_json(self.directory / "route.json", route)
                entry = Path(__file__).resolve().parents[1] / "bridge_mcp.py"
                options = {"start_new_session": True} if os.name != "nt" else {
                    "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
                with (self.directory / "service.log").open("ab") as log:
                    process = subprocess.Popen([sys.executable, str(entry), "--browser-service", str(self.directory)],
                        stdin=subprocess.DEVNULL, stdout=log, stderr=log, **options)
                self._process = process
                threading.Thread(target=process.wait, daemon=True).start()
                deadline = time.monotonic() + route["browser_tool_timeout_seconds"] + 10
                while not self._attach():
                    if process.poll() is not None or time.monotonic() >= deadline:
                        raise RpcError("Connection service failed to start; inspect its private service.log")
                    time.sleep(0.1)
        self.schemas = self._request("schemas")

    def _attach(self):
        try:
            self.endpoint = read_json(self.directory / "endpoint.json")
            if self.endpoint.get("route_sha256") != self.directory.name:
                raise RpcError("Connection service route identity mismatch")
            self._request("status", timeout=2)
            return True
        except (OSError, ValueError, RpcError):
            return False

    def _request(self, operation, timeout=None, **fields):
        message = json.dumps({"token": self.endpoint["token"], "operation": operation, **fields}).encode() + b"\n"
        if len(message) > MAX_MESSAGE:
            raise RpcError("Connection service request exceeds its size limit")
        try:
            with socket.create_connection(("127.0.0.1", self.endpoint["port"]), timeout=2) as stream:
                stream.settimeout(self.timeout if timeout is None else timeout)
                stream.sendall(message)
                with stream.makefile("rb") as reader:
                    raw = reader.readline(MAX_MESSAGE + 1)
                if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                    raise RpcError("Connection service response incomplete; outcome may be unknown")
                result = json.loads(raw)
        except (OSError, ValueError) as exc:
            raise RpcError("Connection service disconnected; outcome may be unknown; no automatic retry") from exc
        if "error" in result:
            raise RpcError(result["error"])
        return result["result"]

    def call(self, name, **arguments):
        return self._request("call", name=name, arguments=arguments)

    def status(self):
        return self._request("status", timeout=2)

    def close(self):
        # Worker lifetime does not own the already-authorized browser transport.
        pass

    def shutdown(self):
        """Explicit maintenance only, never a worker's automatic cleanup."""
        result = self._request("shutdown", timeout=15)
        if self._process is not None:
            self._process.wait(timeout=15)
            result["stopped"] = True
        return result


def serve_connection(directory):
    directory = Path(directory)
    route = read_json(directory / "route.json")
    digest = hashlib.sha256(json.dumps(route, sort_keys=True).encode()).hexdigest()
    if digest != directory.name:
        raise RpcError("Connection service config drift")
    token = secrets.token_hex(32)
    tool_lock = threading.Lock()
    state = {"pid": os.getpid(), "phase": "ready-for-connection", "active_operation": "", "error": ""}
    with (directory / "browser.log").open("ab") as log:
        client = Client(route["browser_command"], env={**os.environ, **route["browser_env"]}, stderr=log,
            timeout=route["browser_tool_timeout_seconds"], connect_timeout=0)

        class Handler(socketserver.StreamRequestHandler):
            def handle(self):
                self.request.settimeout(10)
                try:
                    raw = self.rfile.readline(MAX_MESSAGE + 1)
                    if len(raw) > MAX_MESSAGE or not raw.endswith(b"\n"):
                        return
                    request = json.loads(raw)
                    if not isinstance(request, dict) or not isinstance(request.get("token"), str) or not hmac.compare_digest(request["token"], token):
                        return
                    operation = request.get("operation")
                    if operation == "status":
                        result = dict(state)
                    elif operation == "schemas":
                        result = client.schemas
                    elif operation == "shutdown":
                        held = tool_lock.acquire(blocking=False)
                        waiting_for_auth = state["phase"] == "connecting-browser" and state["active_operation"] == "list_pages"
                        if not held and not waiting_for_auth:
                            raise RpcError("Connection service is busy; shutdown refused")
                        try:
                            state["phase"] = "stopping"
                            client.close()
                            result = {"stop_requested": True}
                            threading.Thread(target=self.server.shutdown, daemon=True).start()
                        finally:
                            if held:
                                tool_lock.release()
                    elif operation == "call":
                        with tool_lock:
                            if state["phase"] in ("stopping", "connection-error"):
                                raise RpcError("Connection service needs explicit inspection; no automatic reconnect")
                            name = request["name"]
                            state.update(phase="connected" if client.browser_connected else "connecting-browser", active_operation=name)
                            try:
                                result = client.call(name, **request.get("arguments", {}))
                            except Exception as exc:
                                # Never replay a call after an unknown timeout.
                                fatal = isinstance(exc, RpcTransportError) or not client.browser_connected
                                state.update(phase="connection-error" if fatal else "connected", error=str(exc))
                                raise
                            finally:
                                state["active_operation"] = ""
                            state["phase"] = "connected" if client.browser_connected else "ready-for-connection"
                    else:
                        raise RpcError("Unknown connection service operation")
                    response = {"result": result}
                except Exception as exc:
                    response = {"error": str(exc)}
                try:
                    self.wfile.write(json.dumps(response).encode() + b"\n")
                except OSError:
                    pass  # The caller must treat its lost receipt as unknown.

        class Server(socketserver.ThreadingTCPServer):
            daemon_threads = True

        try:
            with Server(("127.0.0.1", 0), Handler) as server:
                write_json(directory / "endpoint.json", {"port": server.server_address[1], "token": token,
                    "pid": os.getpid(), "route_sha256": digest})
                os.chmod(directory / "endpoint.json", 0o600)
                server.serve_forever(poll_interval=0.2)
        finally:
            client.close()
