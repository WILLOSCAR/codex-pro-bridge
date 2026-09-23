#!/usr/bin/env python3
"""Normalize the explicit Chrome DevTools MCP observations used by Bridge."""

from __future__ import annotations

import json
import re
from typing import Any, Dict, Mapping

from bridge_store import BridgeError


_PAGE_LINE_RE = re.compile(
    r"^\s*(?P<page_id>[0-9]+):\s+.+\((?P<url>[A-Za-z][A-Za-z0-9+.-]*:\S+)\)"
    r"(?:\s+\[selected\])?\s*$"
)
_JSON_FENCE_RE = re.compile(r"```json\s*(\{.*?\})\s*```", re.DOTALL)


def _aliased_value(item: Mapping[str, Any], aliases: tuple[str, ...], label: str) -> Any:
    present = [(name, item[name]) for name in aliases if name in item]
    if not present:
        raise BridgeError(f"Observation requires {label}")
    first = str(present[0][1] if present[0][1] is not None else "").strip()
    for name, value in present[1:]:
        candidate = str(value if value is not None else "").strip()
        if candidate != first:
            names = ", ".join(alias for alias, _ in present)
            raise BridgeError(f"Conflicting {label} aliases: {names}")
    return present[0][1]


def _content_text(value: Mapping[str, Any], flag: str) -> str:
    if value.get("isError") is True:
        raise BridgeError(f"{flag} MCP observation reports an error")
    content = value.get("content")
    if not isinstance(content, list) or not content:
        raise BridgeError(f"{flag} MCP observation requires a non-empty content array")
    blocks = [
        block.get("text")
        for block in content
        if isinstance(block, dict) and block.get("type") == "text"
    ]
    if len(blocks) != 1 or not isinstance(blocks[0], str):
        raise BridgeError(f"{flag} MCP observation requires exactly one text block")
    return blocks[0]


def _parse_mcp_page_text(value: Mapping[str, Any], flag: str) -> list[Dict[str, Any]]:
    text = _content_text(value, flag)
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines or lines[0].strip() != "## Pages":
        raise BridgeError(f"{flag} MCP text must start with '## Pages'")
    result: list[Dict[str, Any]] = []
    for line in lines[1:]:
        match = _PAGE_LINE_RE.fullmatch(line)
        if not match:
            raise BridgeError(f"{flag} contains an unrecognized MCP page line: {line!r}")
        result.append(
            {"page_id": match.group("page_id"), "url": match.group("url")}
        )
    return result


def normalize_pages_observation(value: Any, flag: str = "--pages-json") -> list[Dict[str, Any]]:
    """Accept the raw list_pages result or its documented structured aliases."""
    if isinstance(value, dict):
        value = _parse_mcp_page_text(value, flag)
    if not isinstance(value, list):
        raise BridgeError(f"{flag} must be a page array or raw MCP list_pages result")
    result: list[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise BridgeError(f"Every {flag} entry must be an object")
        page_id = _aliased_value(item, ("page_id", "pageId", "id"), "page id")
        if "url" not in item:
            raise BridgeError("Every listed page requires url")
        result.append({**item, "page_id": page_id, "url": item["url"]})
    return result


def _parse_mcp_owner_text(value: Mapping[str, Any], flag: str) -> Dict[str, Any]:
    text = _content_text(value, flag)
    matches = _JSON_FENCE_RE.findall(text)
    if len(matches) != 1:
        raise BridgeError(f"{flag} owner MCP text must contain one fenced JSON object")
    try:
        parsed = json.loads(matches[0])
    except json.JSONDecodeError as exc:
        raise BridgeError(f"{flag} owner MCP JSON is invalid: {exc}") from exc
    if not isinstance(parsed, dict) or "owner" not in parsed:
        raise BridgeError(f"{flag} owner MCP JSON requires owner")
    return parsed


def normalize_owners_observation(value: Any, flag: str = "--owners-json") -> list[Dict[str, Any]]:
    """Normalize owner arrays, including page-scoped raw evaluate_script results."""
    if not isinstance(value, list):
        raise BridgeError(
            f"{flag} must be an array; wrap each raw MCP owner result with its pageId"
        )
    result: list[Dict[str, Any]] = []
    for item in value:
        if not isinstance(item, dict):
            raise BridgeError(f"Every {flag} entry must be an object")
        page_id = _aliased_value(item, ("page_id", "pageId", "id"), "page id")
        if "observation" in item and "content" in item:
            raise BridgeError(f"{flag} entry cannot contain both observation and content")
        raw = item.get("observation")
        if raw is None and "content" in item:
            raw = {
                key: item[key]
                for key in ("content", "isError")
                if key in item
            }
        raw_present = raw is not None
        raw_owner = None
        if raw_present:
            if not isinstance(raw, dict):
                raise BridgeError(f"{flag} observation must be an MCP result object")
            raw_owner = _parse_mcp_owner_text(raw, flag)["owner"]
        aliases = tuple(
            alias
            for alias in ("owner_token", "tab_owner_token", "owner")
            if alias in item
        )
        if aliases:
            owner = _aliased_value(item, aliases, "owner token")
            if raw_present and str(owner or "").strip() != str(raw_owner or "").strip():
                raise BridgeError("Conflicting owner token aliases and raw MCP owner result")
        elif raw_present:
            owner = raw_owner
        else:
            raise BridgeError("Every owner observation requires an owner token or MCP observation")
        result.append({"page_id": page_id, "owner_token": str(owner or "").strip()})
    return result


def parse_json_observation(value: str, flag: str, *, owners: bool = False) -> list[Dict[str, Any]]:
    try:
        parsed = json.loads(value)
    except json.JSONDecodeError as exc:
        raise BridgeError(f"{flag} must be valid JSON: {exc}") from exc
    if owners:
        return normalize_owners_observation(parsed, flag)
    return normalize_pages_observation(parsed, flag)
