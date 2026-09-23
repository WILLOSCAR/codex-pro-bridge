"""Digest-verified staging across execution-host and browser-host path namespaces."""

from __future__ import annotations

import contextlib
import hashlib
import os
import re
import shutil
import tempfile
from pathlib import Path, PureWindowsPath
from typing import Any, Dict

from bridge_store import BridgeError, file_lock, file_sha256
from host_config import BrowserHostConfig, load_browser_host_config


STAGING_PREFIX = "codex-bridge-"
DEFAULT_BROWSER_STAGING_ROOT = PureWindowsPath(r"C:\CodexBridge\staging")


def staging_wsl_root() -> Path:
    """Return the execution-host staging root.

    The historical name is retained for protocol compatibility even when the
    execution host is native Windows rather than WSL.
    """

    return load_browser_host_config().execution_root


def staging_windows_root():
    """Return the browser-visible Windows staging root.

    A generic display-only default keeps attachment-free preflight compatible.
    Every staging operation still calls ``load_browser_host_config`` and thus
    requires an explicit mapping on non-Windows execution hosts.
    """

    try:
        return load_browser_host_config().browser_root
    except BridgeError:
        return DEFAULT_BROWSER_STAGING_ROOT


def _safe_component(value: str, *, fallback: str, limit: int) -> str:
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", value.strip()).strip("-.")
    return (safe or fallback)[:limit].rstrip("-.") or fallback


def _ensure_root(config: BrowserHostConfig) -> Path:
    root = config.execution_root
    root.mkdir(parents=True, exist_ok=True)
    if root.is_symlink() or not root.is_dir():
        raise BridgeError(f"Browser staging root must be a regular directory: {root}")
    return root.resolve()


def _canonical_direct_child(
    value: str | Path, *, config: BrowserHostConfig, must_exist: bool
) -> Path:
    root = _ensure_root(config)
    raw = Path(value).expanduser()
    if not raw.is_absolute():
        raise BridgeError("Browser staging paths must be absolute on the execution host")
    if raw.is_symlink():
        raise BridgeError(f"Staged path must not be a symlink: {raw}")
    path = raw.resolve(strict=must_exist)
    if path.parent != root:
        raise BridgeError(f"Staged path must be a direct child of {root}: {path}")
    if not path.name.startswith(STAGING_PREFIX):
        raise BridgeError(f"Staged filename must start with {STAGING_PREFIX!r}")
    if must_exist and not path.is_file():
        raise BridgeError(f"Staged path is not a regular file: {path}")
    return path


def browser_path_for(staged_path: str | Path, *, config: BrowserHostConfig) -> str:
    path = _canonical_direct_child(staged_path, config=config, must_exist=True)
    return str(config.browser_root / path.name)


def stage_browser_file(source: str | Path, *, thread_id: str) -> Dict[str, Any]:
    config = load_browser_host_config()
    source_path = Path(source).expanduser()
    if not source_path.is_absolute():
        raise BridgeError("Source bundle path must be absolute")
    if source_path.is_symlink():
        raise BridgeError(f"Source bundle must not be a symlink: {source_path}")
    source_path = source_path.resolve()
    if not source_path.is_file():
        raise BridgeError(f"Source bundle must be a regular non-symlink file: {source_path}")

    root = _ensure_root(config)
    digest = file_sha256(source_path)
    safe_thread = _safe_component(thread_id, fallback="thread", limit=48)
    safe_stem = _safe_component(source_path.stem, fallback="bundle", limit=64)
    suffix = _safe_component(source_path.suffix, fallback=".bin", limit=12)
    if not suffix.startswith("."):
        suffix = f".{suffix}"
    filename = f"{STAGING_PREFIX}{safe_thread}-{digest[:12]}-{safe_stem}{suffix}"
    target = _canonical_direct_child(root / filename, config=config, must_exist=False)

    with file_lock(config.lock_path):
        reused = False
        if target.exists():
            if target.is_symlink() or not target.is_file():
                raise BridgeError(f"Existing staged target is unsafe: {target}")
            if file_sha256(target) != digest:
                raise BridgeError(f"Existing staged target has the wrong digest: {target}")
            reused = True
        else:
            fd, temp_name = tempfile.mkstemp(prefix=".codex-bridge-stage-", dir=str(root))
            temp_path = Path(temp_name)
            try:
                with os.fdopen(fd, "wb") as output, source_path.open("rb") as input_handle:
                    shutil.copyfileobj(input_handle, output, length=1024 * 1024)
                    output.flush()
                    os.fsync(output.fileno())
                if file_sha256(temp_path) != digest:
                    raise BridgeError("Staged copy digest does not match its source")
                os.replace(temp_path, target)
            except Exception:
                with contextlib.suppress(FileNotFoundError):
                    temp_path.unlink()
                raise

    result = {
        "ready": True,
        "topology": config.topology,
        "config_source": config.source,
        "source_path": str(source_path),
        "source_sha256": digest,
        "staged_execution_path": str(target),
        "staged_browser_path": browser_path_for(target, config=config),
        "attachment_name": target.name,
        "size_bytes": target.stat().st_size,
        "reused": reused,
    }
    # Keep the established receipt field names while exposing topology-neutral
    # names to new callers.
    result["staged_wsl_path"] = result["staged_execution_path"]
    result["staged_windows_path"] = result["staged_browser_path"]
    return result


def verify_staged_file(
    staged_path: str | Path,
    *,
    source_path: str | Path | None = None,
    expected_sha256: str = "",
    expected_browser_path: str = "",
) -> Dict[str, Any]:
    config = load_browser_host_config()
    path = _canonical_direct_child(staged_path, config=config, must_exist=True)
    staged_digest = file_sha256(path)
    expected = expected_sha256.strip().lower()
    if expected and not re.fullmatch(r"[0-9a-f]{64}", expected):
        raise BridgeError("Expected SHA-256 must contain exactly 64 lowercase hex characters")
    if expected and staged_digest != expected:
        raise BridgeError("Staged file SHA-256 does not match the expected digest")

    source_digest = ""
    resolved_source = ""
    if source_path:
        source = Path(source_path).expanduser()
        if not source.is_absolute():
            raise BridgeError("Source bundle path must be absolute")
        if source.is_symlink():
            raise BridgeError(f"Source bundle must not be a symlink: {source}")
        source = source.resolve()
        if not source.is_file():
            raise BridgeError(f"Source bundle must be a regular non-symlink file: {source}")
        source_digest = file_sha256(source)
        resolved_source = str(source)
        if source_digest != staged_digest:
            raise BridgeError("Browser-host staged file does not match the source bundle")

    browser_path = browser_path_for(path, config=config)
    if expected_browser_path and expected_browser_path != browser_path:
        raise BridgeError("Observed browser upload path does not match the configured mapping")
    result = {
        "verified": True,
        "topology": config.topology,
        "source_path": resolved_source,
        "source_sha256": source_digest,
        "staged_execution_path": str(path),
        "staged_browser_path": browser_path,
        "staged_sha256": staged_digest,
        "attachment_name": path.name,
        "size_bytes": path.stat().st_size,
    }
    result["staged_wsl_path"] = result["staged_execution_path"]
    result["staged_windows_path"] = result["staged_browser_path"]
    return result


def cleanup_staged_file(staged_path: str | Path, *, expected_sha256: str) -> Dict[str, Any]:
    config = load_browser_host_config()
    digest = expected_sha256.strip().lower()
    if not re.fullmatch(r"[0-9a-f]{64}", digest):
        raise BridgeError("Cleanup requires the exact 64-character staged SHA-256")
    with file_lock(config.lock_path):
        path = _canonical_direct_child(staged_path, config=config, must_exist=True)
        actual = file_sha256(path)
        if actual != digest:
            raise BridgeError("Refusing cleanup because the staged file digest changed")
        size = path.stat().st_size
        browser_path = str(config.browser_root / path.name)
        path.unlink()
    result = {
        "cleaned": True,
        "topology": config.topology,
        "staged_execution_path": str(path),
        "staged_browser_path": browser_path,
        "sha256": actual,
        "size_bytes": size,
    }
    result["staged_wsl_path"] = result["staged_execution_path"]
    result["staged_windows_path"] = result["staged_browser_path"]
    return result
