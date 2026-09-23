#!/usr/bin/env python3
"""Run Bridge's persistent submit/status/wait/result MCP or a detached worker."""
from __future__ import annotations

import argparse
import concurrent.futures
import json
from pathlib import Path
import sys
import threading

sys.path.insert(0, str(Path(__file__).resolve().parents[2] / ".shared"))
from bridge_runtime.jobs import Jobs, run_worker


def serve(jobs, source=sys.stdin, sink=sys.stdout):
    lock = threading.Lock()
    def emit(value):
        with lock:
            sink.write(json.dumps(value, ensure_ascii=True) + "\n")
            sink.flush()

    identifiers = {"job_id": {"type": "string", "pattern": "^[0-9a-f]{64}$"}}
    definitions = {
        "bridge_submit": ("Submit a frozen handoff once; returns a durable job ID immediately.", {
            "handoff_path": {"type": "string"}, "handoff_sha256": {"type": "string"}},
            ["handoff_path", "handoff_sha256"]),
        "bridge_status": ("Read compact job state without browser actions.", identifiers, ["job_id"]),
        "bridge_wait": ("Wait up to 55 seconds; a wait timeout never stops the worker.", {
            **identifiers, "seconds": {"type": "number", "minimum": 0, "maximum": 55}}, ["job_id"]),
        "bridge_result": ("Return immutable result paths and verify their digests; no answer paraphrase.", identifiers, ["job_id"]),
        "bridge_resume": ("Recover the same interrupted job; never replay an uncertain upload or Send.", identifiers, ["job_id"]),
    }
    methods = {"bridge_" + name: getattr(jobs, name) for name in ("submit", "status", "wait", "result", "resume")}
    def dispatch(message):
        identifier = message.get("id")
        method = message.get("method")
        if "id" not in message:
            return
        try:
            params = message.get("params", {})
            if method == "initialize":
                result = {"protocolVersion": "2024-11-05", "capabilities": {"tools": {}},
                          "serverInfo": {"name": "codex-pro-bridge", "version": "0.1.0"}}
            elif method == "ping":
                result = {}
            elif method == "tools/list":
                result = {"tools": [{"name": name, "description": desc, "inputSchema": {
                    "type": "object", "properties": props, "required": required,
                    "additionalProperties": False}} for name, (desc, props, required) in definitions.items()]}
            elif method == "tools/call":
                name, arguments = params["name"], params.get("arguments", {})
                if name not in definitions:
                    raise ValueError("Unknown Bridge tool")
                _, properties, required = definitions[name]
                if not isinstance(arguments, dict) or set(arguments) - set(properties) or set(required) - set(arguments):
                    raise ValueError("Invalid tool arguments")
                value = methods[name](**arguments)
                result = {"content": [{"type": "text", "text": json.dumps(value, ensure_ascii=True)}],
                          "structuredContent": value}
            else:
                emit({"jsonrpc": "2.0", "id": identifier, "error": {"code": -32601, "message": "Method not found"}})
                return
        except Exception as exc:
            result = {"isError": True, "content": [{"type": "text", "text": str(exc)}]}
        emit({"jsonrpc": "2.0", "id": identifier, "result": result})

    # Wait calls must not block status/submit for unrelated jobs.
    with concurrent.futures.ThreadPoolExecutor(max_workers=8) as executor:
        for line in source:
            try:
                value = json.loads(line)
                if not isinstance(value, dict):
                    raise ValueError("Expected JSON-RPC object")
                executor.submit(dispatch, value)
            except ValueError:
                emit({"jsonrpc": "2.0", "id": None, "error": {"code": -32700, "message": "Parse error"}})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--config", type=Path)
    mode.add_argument("--worker", type=Path)
    mode.add_argument("--browser-service", type=Path)
    args = parser.parse_args()
    if args.browser_service:
        from bridge_runtime.connection_service import serve_connection
        serve_connection(args.browser_service)
    elif args.worker:
        run_worker(args.worker)
    else:
        serve(Jobs(args.config))


if __name__ == "__main__":
    main()
