"""Portable browser-host configuration for local and mapped Windows uploads."""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path, PureWindowsPath
from typing import Any, Mapping

from bridge_store import BridgeError


CONFIG_ENV = "CODEX_PRO_BRIDGE_HOST_CONFIG"
EXECUTION_ROOT_ENV = "CODEX_BRIDGE_STAGING_EXECUTION_ROOT"
BROWSER_ROOT_ENV = "CODEX_BRIDGE_STAGING_BROWSER_ROOT"
LOCK_PATH_ENV = "CODEX_BRIDGE_STAGING_LOCK"
TOPOLOGIES = {"windows-native", "wsl-windows"}
LEGACY_EXECUTION_ROOT_ENV = "CODEX_BRIDGE_STAGING_WSL_ROOT"
LEGACY_BROWSER_ROOT_ENV = "CODEX_BRIDGE_STAGING_WINDOWS_ROOT"


@dataclass(frozen=True)
class BrowserHostConfig:
    topology: str
    execution_root: Path
    browser_root: PureWindowsPath
    lock_path: Path
    source: str


def _read_json(path: Path) -> Mapping[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise BridgeError(f"Cannot read browser-host config: {path}") from exc
    if not isinstance(value, Mapping):
        raise BridgeError("Browser-host config must be a JSON object")
    return value


def _required_env(name: str, *, field: str) -> str:
    value = os.environ.get(name, "").strip()
    if not value:
        raise BridgeError(f"Browser-host config field {field!r} requires environment variable {name}")
    return value


def _environment_name(staging: Mapping[str, Any], field: str, default: str) -> str:
    value = staging.get(field, default)
    if not isinstance(value, str) or not value.strip():
        raise BridgeError(f"Browser-host config field staging.{field} must be a non-empty string")
    return value.strip()


def _absolute_execution_path(value: str, *, field: str) -> Path:
    path = Path(value).expanduser()
    if not path.is_absolute():
        raise BridgeError(f"Browser-host config field {field!r} must resolve to an absolute path")
    return path


def _configured(path: Path) -> BrowserHostConfig:
    value = _read_json(path)
    if value.get("schema_version") != 1:
        raise BridgeError("Browser-host config schema_version must be 1")
    topology = str(value.get("topology", "")).strip()
    if topology not in TOPOLOGIES:
        raise BridgeError("Browser-host topology must be windows-native or wsl-windows")
    staging = value.get("staging")
    if not isinstance(staging, Mapping):
        raise BridgeError("Browser-host config requires a staging object")
    execution_env = _environment_name(
        staging, "execution_root_env", EXECUTION_ROOT_ENV
    )
    browser_env = _environment_name(staging, "browser_root_env", BROWSER_ROOT_ENV)
    lock_env = _environment_name(staging, "lock_path_env", LOCK_PATH_ENV)
    execution_root = _absolute_execution_path(
        _required_env(execution_env, field="staging.execution_root_env"),
        field="staging.execution_root_env",
    )
    browser_root = PureWindowsPath(
        _required_env(browser_env, field="staging.browser_root_env")
    )
    if not browser_root.is_absolute():
        raise BridgeError("Configured browser staging root must be an absolute Windows path")
    lock_path = _absolute_execution_path(
        _required_env(lock_env, field="staging.lock_path_env"),
        field="staging.lock_path_env",
    )
    if topology == "windows-native" and os.name != "nt":
        raise BridgeError("windows-native topology requires native Windows Python")
    return BrowserHostConfig(topology, execution_root, browser_root, lock_path, str(path))


def _windows_default() -> BrowserHostConfig:
    workspace_root = Path.cwd().resolve()
    base = workspace_root / ".codex" / "codex-pro-bridge"
    root = base / "browser-staging"
    return BrowserHostConfig(
        topology="windows-native",
        execution_root=root,
        browser_root=PureWindowsPath(str(root)),
        lock_path=base / "browser-staging.lock",
        source="windows-workspace-default",
    )


def _legacy_environment() -> BrowserHostConfig | None:
    """Load the pre-config-file path variables when both halves are explicit."""

    execution_value = os.environ.get(LEGACY_EXECUTION_ROOT_ENV, "").strip()
    browser_value = os.environ.get(LEGACY_BROWSER_ROOT_ENV, "").strip()
    if not execution_value and not browser_value:
        return None
    if not execution_value or not browser_value:
        raise BridgeError(
            f"{LEGACY_EXECUTION_ROOT_ENV} and {LEGACY_BROWSER_ROOT_ENV} must be set together"
        )
    execution_root = _absolute_execution_path(
        execution_value, field=LEGACY_EXECUTION_ROOT_ENV
    )
    browser_root = PureWindowsPath(browser_value)
    if not browser_root.is_absolute():
        raise BridgeError(f"{LEGACY_BROWSER_ROOT_ENV} must be an absolute Windows path")
    lock_value = os.environ.get(LOCK_PATH_ENV, "").strip()
    lock_path = (
        _absolute_execution_path(lock_value, field=LOCK_PATH_ENV)
        if lock_value
        else execution_root.parent / "browser-staging.lock"
    )
    return BrowserHostConfig(
        topology="windows-native" if os.name == "nt" else "wsl-windows",
        execution_root=execution_root,
        browser_root=browser_root,
        lock_path=lock_path,
        source="legacy-environment",
    )


def load_browser_host_config() -> BrowserHostConfig:
    """Load the optional config, or the safe Windows-native default.

    Non-Windows execution cannot infer a Windows path mapping. It must provide
    ``CODEX_PRO_BRIDGE_HOST_CONFIG`` so the two path namespaces are explicit.
    """

    configured = os.environ.get(CONFIG_ENV, "").strip()
    if configured:
        path = Path(configured).expanduser()
        if not path.is_absolute():
            raise BridgeError(f"{CONFIG_ENV} must be an absolute path")
        return _configured(path.resolve())
    legacy = _legacy_environment()
    if legacy is not None:
        return legacy
    if os.name == "nt":
        return _windows_default()
    raise BridgeError(
        f"Browser staging on this execution host requires {CONFIG_ENV}; "
        "use the WSL-to-Windows example config"
    )
