"""Durable job envelopes. Canonical send state remains in bridge_attempts."""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import threading
import time

from bridge_store import BridgeError, atomic_write_text, file_lock, file_sha256, now_iso
from executor_handoff import validate_handoff
from prepare_review import read_request


def read_json(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write_json(path, value):
    atomic_write_text(Path(path), json.dumps(value, ensure_ascii=True, sort_keys=True) + "\n")


def load_config(path):
    config = read_json(path)
    if config.get("browser_transport", "stdio") not in ("stdio", "persistent"):
        raise BridgeError("browser_transport must be stdio or persistent")
    for key, default in (("browser_connect_timeout_seconds", 300), ("browser_tool_timeout_seconds", 90)):
        value = config.get(key, default)
        if isinstance(value, bool) or not isinstance(value, (int, float)) or not 1 <= value <= 1800:
            raise BridgeError(f"{key} must be between 1 and 1800 seconds")
    for key in ("state_dir", "allowed_repos", "browser_command", "ui"):
        if key not in config:
            raise BridgeError(f"Runtime config lacks {key}")
    if not Path(config["state_dir"]).is_absolute():
        raise BridgeError("state_dir must be absolute")
    roots = config["allowed_repos"]
    if not isinstance(roots, list) or not roots or any(not Path(p).is_absolute() for p in roots):
        raise BridgeError("allowed_repos must contain explicit absolute repository roots")
    command = config["browser_command"]
    if not isinstance(command, list) or not command or any(not isinstance(v, str) for v in command):
        raise BridgeError("browser_command must be an argv array")
    if not isinstance(config["ui"], dict):
        raise BridgeError("ui must be an object")
    ui = config["ui"]
    if ui.get("control_layout", "independent") not in ("independent", "nested-slider"):
        raise BridgeError("Unsupported model control layout")
    if ui.get("control_layout") == "nested-slider":
        for key in ("thinking_slider", "thinking_keyboard_control"):
            if not isinstance(ui.get(key), str) or not ui[key].strip() or "<" in ui[key]:
                raise BridgeError(f"Nested UI profile requires an observed selector for {key}")
        positions = ui.get("thinking_positions")
        if not isinstance(positions, dict) or not positions or any(
                not isinstance(k, str) or not k.strip() or isinstance(v, bool)
                or not isinstance(v, int) or v < 0 for k, v in positions.items()):
            raise BridgeError("Nested UI profile requires observed integer thinking_positions")
    for key in ("model", "thinking", "composer", "attachment_chip"):
        if not isinstance(ui.get(key), str) or not ui[key].strip() or "<" in ui[key]:
            raise BridgeError(f"UI profile requires an observed selector for {key}")
    for key in ("model_menu_labels", "thinking_menu_labels", "attachment_labels", "upload_labels", "send_labels"):
        if not isinstance(ui.get(key), list) or not ui[key] or any(
                not isinstance(label, str) or not label.strip() or "<" in label for label in ui[key]):
            raise BridgeError(f"UI profile requires observed accessible names for {key}")
    if any("<" in str(value) for value in (config["state_dir"], *roots, *command)):
        raise BridgeError("Runtime config still contains example placeholders")
    return config


def validate_inputs(handoff_path, expected_hash, config):
    path = Path(handoff_path).expanduser().resolve(strict=True)
    if file_sha256(path) != expected_hash:
        raise BridgeError("Frozen handoff SHA-256 drift")
    h = validate_handoff(read_json(path))
    repo = Path(h["repo"]).resolve(strict=True)
    if repo not in {Path(p).resolve() for p in config["allowed_repos"]}:
        raise BridgeError("Repository is not in runtime allowed_repos")
    if not path.is_relative_to(repo):
        raise BridgeError("Handoff must stay in its repository")
    if file_sha256(Path(h["request_file"])) != h["request_sha256"]:
        raise BridgeError("Frozen request SHA-256 drift")
    request = read_request(Path(h["request_file"]))
    for field in ("repo", "bridge_thread_id", "context_policy", "max_files"):
        if str(request[field]) != str(h.get(field)):
            raise BridgeError(f"Handoff and request disagree on {field}")
    if request.get("bridge_project_id", "") != h.get("bridge_project_id", ""):
        raise BridgeError("Handoff and request disagree on Project")
    if "file_digests" not in request:
        raise BridgeError("Execution requires frozen file_digests")
    if h.get("remote_project_id"):
        for key in ("workspace", "account"):
            if not isinstance(config["ui"].get(key), str) or not config["ui"][key].strip() or "<" in config["ui"][key]:
                raise BridgeError(f"Project UI profile requires an observed {key} selector")
    return h, request


class Jobs:
    def __init__(self, config_path):
        self.config_path = Path(config_path).resolve(strict=True)
        self.config = load_config(self.config_path)
        self.root = Path(self.config["state_dir"]).resolve()
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)

    def directory(self, job_id):
        import re
        if not isinstance(job_id, str) or not re.fullmatch(r"[0-9a-f]{64}", job_id):
            raise BridgeError("job_id must be the returned 64-character identifier")
        return self.root / job_id

    def submit(self, handoff_path, handoff_sha256):
        h, _ = validate_inputs(handoff_path, handoff_sha256, self.config)
        if h["mode"] != "prepare-and-run":
            raise BridgeError("Submit requires a new v2 handoff; recover existing runtime jobs with bridge_resume")
        job_id = hashlib.sha256((str(Path(h["repo"]).resolve()) + "\n" + handoff_sha256).encode()).hexdigest()
        directory = self.directory(job_id)
        with file_lock(self.root / ".submit.lock"):
            if (directory / "job.json").exists():
                return self.status(job_id)
            directory.mkdir(mode=0o700)
            # The job freezes configuration too; resume cannot change its browser route.
            write_json(directory / "config.json", self.config)
            write_json(directory / "job.json", {
                "schema_version": 1, "job_id": job_id, "state": "queued", "stage": "queued",
                "handoff_path": str(Path(handoff_path).resolve()), "handoff_sha256": handoff_sha256,
                "config_sha256": file_sha256(directory / "config.json"),
                "repo": h["repo"], "bridge_thread_id": h["bridge_thread_id"],
                "created_at": now_iso(), "updated_at": now_iso(), "may_resend": False,
            })
            self._spawn(directory)
        return self.status(job_id)

    def _spawn(self, directory):
        entry = Path(__file__).resolve().parents[1] / "bridge_mcp.py"
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        options = {"start_new_session": True} if os.name != "nt" else {
            "creationflags": subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.DETACHED_PROCESS}
        with (directory / "worker.log").open("ab") as log:
            process = subprocess.Popen([sys.executable, str(entry), "--worker", str(directory)],
                                       stdin=subprocess.DEVNULL, stdout=log, stderr=log, env=env, **options)
        # Reap children while this MCP lives; detachment keeps workers alive on EOF.
        threading.Thread(target=process.wait, daemon=True).start()

    def status(self, job_id):
        directory = self.directory(job_id)
        job = read_json(directory / "job.json")
        result = {key: job[key] for key in (
            "job_id", "state", "stage", "bridge_thread_id", "updated_at", "may_resend")}
        for key in ("attempt_id", "conversation_url", "remote_turn_id", "error", "result", "timings", "browser_phase"):
            if key in job:
                result[key] = job[key]
        heartbeat = directory / "heartbeat"
        try:
            result["worker_recently_alive"] = time.time() - heartbeat.stat().st_mtime < 90
        except FileNotFoundError:
            result["worker_recently_alive"] = False
        result["next_action"] = ("result" if job["state"] == "complete" else
                                 "inspect-blocker" if job["state"] == "blocked" else
                                 "wait" if result["worker_recently_alive"] else "resume")
        return result

    def wait(self, job_id, seconds=30):
        if isinstance(seconds, bool) or not isinstance(seconds, (int, float)) or not 0 <= seconds <= 55:
            raise BridgeError("wait seconds must be between 0 and 55")
        deadline = time.monotonic() + seconds
        while True:
            result = self.status(job_id)
            if result["state"] in {"complete", "blocked"} or time.monotonic() >= deadline:
                return result
            time.sleep(min(0.5, max(0, deadline - time.monotonic())))

    def resume(self, job_id):
        result = self.status(job_id)
        if result["state"] == "complete":
            return result
        if not result["worker_recently_alive"]:
            self._spawn(self.directory(job_id))
        return self.status(job_id)

    def result(self, job_id):
        result = self.status(job_id)
        if result["state"] != "complete":
            return result
        receipt = result["result"]
        for path_key, hash_key in (("answer_path", "answer_sha256"), ("turn_path", "turn_sha256")):
            if file_sha256(Path(receipt[path_key])) != receipt[hash_key]:
                raise BridgeError("Saved result digest drift")
        return result


@contextlib.contextmanager
def worker_lock(directory):
    """Single owner across MCP processes; duplicate resume never queues a worker."""
    handle = (directory / "worker.lock").open("a+b")
    acquired = False
    try:
        if os.name == "nt":
            import msvcrt
            handle.seek(0, 2)
            if handle.tell() == 0:
                handle.write(b"\0")
                handle.flush()
            handle.seek(0)
            try:
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
                acquired = True
            except OSError:
                pass
        else:
            import fcntl
            try:
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
                acquired = True
            except BlockingIOError:
                pass
        yield acquired
    finally:
        if acquired:
            if os.name == "nt":
                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        handle.close()


class Job:
    def __init__(self, directory):
        self.directory = Path(directory)
        self.path = self.directory / "job.json"
        self.data = read_json(self.path)

    def update(self, **fields):
        self.data.update(fields, updated_at=now_iso())
        write_json(self.path, self.data)


def run_worker(directory):
    from .pipeline import execute
    directory = Path(directory).resolve(strict=True)
    with worker_lock(directory) as acquired:
        if not acquired:
            return
        job = Job(directory)
        if job.data["state"] == "complete":
            return
        stop = threading.Event()
        def pulse():
            while not stop.is_set():
                (directory / "heartbeat").touch()
                stop.wait(5)
        thread = threading.Thread(target=pulse, daemon=True)
        thread.start()
        try:
            if file_sha256(directory / "config.json") != job.data["config_sha256"]:
                raise BridgeError("Runtime config drift")
            execute(job, load_config(directory / "config.json"))
        except Exception as exc:
            job.update(state="blocked", error=f"{type(exc).__name__}: {exc}", may_resend=False)
        finally:
            stop.set()
            thread.join(timeout=6)
            (directory / "heartbeat").unlink(missing_ok=True)
