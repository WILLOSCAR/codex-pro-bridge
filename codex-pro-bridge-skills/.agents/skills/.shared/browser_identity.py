#!/usr/bin/env python3
"""Shared ChatGPT page and conversation ownership validation."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Dict, Mapping, Optional, Sequence
from urllib.parse import urlparse

from bridge_store import (
    BridgeError,
    assert_browser_lease_held,
    inspect_browser_tab_owner,
    mark_browser_tab_bound,
    normalize_browser_profile,
    promote_browser_bootstrap,
)
from browser_observations import normalize_owners_observation, normalize_pages_observation


def conversation_identity_from_url(value: str) -> tuple[str, str]:
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or hostname not in {"chatgpt.com", "chat.openai.com"}:
        raise BridgeError("Observed page URL must be an https ChatGPT URL")
    parts = [part for part in parsed.path.split("/") if part]
    if len(parts) == 2 and parts[0] == "c":
        return "", parts[1]
    if len(parts) == 4 and parts[0] == "g" and parts[2] == "c":
        return parts[1], parts[3]
    raise BridgeError("Observed page URL must identify one exact ChatGPT conversation")


def project_url_matches(url_project_id: str, expected_project_id: str) -> bool:
    if not expected_project_id:
        return not url_project_id
    return url_project_id == expected_project_id or url_project_id.startswith(
        expected_project_id + "-"
    )


def bootstrap_destination_from_url(value: str, expected_project_id: str) -> str:
    """Validate a new-chat landing URL and return its observed Project segment."""
    parsed = urlparse(value)
    hostname = (parsed.hostname or "").lower()
    if parsed.scheme != "https" or hostname not in {"chatgpt.com", "chat.openai.com"}:
        raise BridgeError("Observed bootstrap URL must be an https ChatGPT URL")
    if parsed.fragment:
        raise BridgeError("Bootstrap URL must not contain a fragment")
    parts = [part for part in parsed.path.split("/") if part]
    expected_project_id = expected_project_id.strip()
    if not expected_project_id:
        if parsed.query:
            raise BridgeError("Standalone bootstrap URL must not contain query parameters")
        if parts:
            raise BridgeError("Standalone bootstrap must start on the ChatGPT home page")
        return ""
    if parsed.query and parsed.query != "tab=chats":
        raise BridgeError("Project bootstrap URL permits only the exact query ?tab=chats")
    if not (
        len(parts) in {2, 3}
        and parts[0] == "g"
        and (len(parts) == 2 or parts[2] == "project")
        and project_url_matches(parts[1], expected_project_id)
    ):
        raise BridgeError("Project bootstrap URL does not identify the expected Project home")
    return parts[1]


def canonical_bootstrap_url(expected_project_id: str) -> str:
    project_id = expected_project_id.strip()
    if project_id:
        return f"https://chatgpt.com/g/{project_id}/project"
    return "https://chatgpt.com/"


def normalize_page_id(value: Any) -> str:
    if isinstance(value, bool):
        raise BridgeError("MCP pageId must be a positive integer")
    raw = str(value).strip()
    if not raw.isdecimal() or int(raw) <= 0:
        raise BridgeError("MCP pageId must be a positive integer")
    return str(int(raw))


def iter_chatgpt_pages(pages: Sequence[Mapping[str, Any]]) -> list[Dict[str, str]]:
    pages = normalize_pages_observation(pages)
    result = []
    seen = set()
    for item in pages:
        page_id = normalize_page_id(item["page_id"])
        if page_id in seen:
            raise BridgeError(f"Duplicate MCP pageId in page list: {page_id}")
        seen.add(page_id)
        url = str(item["url"] or "").strip()
        parsed = urlparse(url)
        if not url or not parsed.scheme:
            raise BridgeError(f"Listed page {page_id} has an invalid URL")
        if parsed.scheme != "https" or (parsed.hostname or "").lower() not in {
            "chatgpt.com",
            "chat.openai.com",
        }:
            continue
        result.append({"page_id": page_id, "url": url})
    return result


def conversation_id_from_url_or_none(value: str) -> Optional[str]:
    try:
        return conversation_identity_from_url(value)[1]
    except BridgeError:
        return None


def match_listed_pages(
    pages: Sequence[Mapping[str, Any]],
    *,
    expected_project_id: str,
    expected_conversation_id: str = "",
    bootstrap: bool = False,
) -> Dict[str, Any]:
    chatgpt_pages = iter_chatgpt_pages(pages)
    expected_project_id = expected_project_id.strip()
    expected_conversation_id = expected_conversation_id.strip()
    canonical_matches = []
    conversation_matches = []
    noncanonical_project_home = []
    for page in chatgpt_pages:
        url = page["url"]
        if bootstrap:
            try:
                bootstrap_destination_from_url(url, expected_project_id)
                canonical_matches.append(page)
            except BridgeError:
                parsed = urlparse(url)
                parts = [part for part in parsed.path.split("/") if part]
                if (
                    expected_project_id
                    and len(parts) in {2, 3}
                    and parts[0] == "g"
                    and (len(parts) == 2 or parts[2] == "project")
                    and project_url_matches(parts[1], expected_project_id)
                ):
                    noncanonical_project_home.append(page)
        if expected_conversation_id:
            try:
                project_id, conversation_id = conversation_identity_from_url(url)
            except BridgeError:
                continue
            if conversation_id == expected_conversation_id and project_url_matches(
                project_id, expected_project_id
            ):
                conversation_matches.append(page)
    return {
        "canonical_matches": canonical_matches,
        "conversation_matches": conversation_matches,
        "noncanonical_project_home": noncanonical_project_home,
        "chatgpt_pages": chatgpt_pages,
    }


def _owner_map(
    owners: Optional[Sequence[Mapping[str, Any]]],
    *,
    listed_page_ids: set[str],
) -> Optional[Dict[str, str]]:
    if owners is None:
        return None
    owners = normalize_owners_observation(owners)
    result = {}
    for item in owners:
        page_id = normalize_page_id(item["page_id"])
        if page_id not in listed_page_ids:
            raise BridgeError(f"Owner observation pageId was not listed by MCP: {page_id}")
        if page_id in result:
            raise BridgeError(f"Duplicate owner observation for MCP pageId: {page_id}")
        result[page_id] = str(item["owner_token"] or "").strip()
    return result


def _hold(reason: str, *, claim: Mapping[str, Any], **extra: Any) -> Dict[str, Any]:
    return {
        "action": "hold",
        "reason": reason,
        "tab_bound": bool(claim.get("tab_bound", False)),
        **extra,
    }


def resolve_owned_tab(
    repo: Path,
    *,
    claim_token: str,
    thread_id: str,
    browser_profile: str,
    expected_project_id: str,
    pages: Sequence[Mapping[str, Any]],
    owners: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Resolve one physical browser tab from a complete MCP page observation."""
    claim = assert_browser_lease_held(repo, token=claim_token)
    expected_project_id = expected_project_id.strip()
    if claim.get("thread_id") != thread_id.strip():
        raise BridgeError("Browser claim belongs to a different Bridge Thread")
    if normalize_browser_profile(str(claim.get("browser_profile", ""))) != normalize_browser_profile(
        browser_profile
    ):
        raise BridgeError("Browser claim profile does not match the observed profile")
    if claim.get("expected_remote_project_id", "") != expected_project_id:
        raise BridgeError("Browser claim Project does not match the expected destination")

    bootstrap = bool(claim.get("bootstrap", False))
    expected_conversation_id = str(claim.get("expected_conversation_id", ""))
    expected_scope = "project" if expected_project_id else "profile"
    if bootstrap:
        if claim.get("scope") != expected_scope or expected_conversation_id:
            raise BridgeError("Malformed bootstrap claim scope or conversation identity")
    elif claim.get("scope") != "conversation" or not expected_conversation_id:
        raise BridgeError("Tab resolution requires a bootstrap or conversation claim")

    matches = match_listed_pages(
        pages,
        expected_project_id=expected_project_id,
        expected_conversation_id=expected_conversation_id,
        bootstrap=bootstrap,
    )
    chatgpt_pages = matches["chatgpt_pages"]
    listed_ids = {page["page_id"] for page in chatgpt_pages}
    owner_map = _owner_map(owners, listed_page_ids=listed_ids)
    pages_to_read = chatgpt_pages
    missing_owner_ids = [
        page["page_id"]
        for page in pages_to_read
        if owner_map is None or page["page_id"] not in owner_map
    ]
    if missing_owner_ids:
        return {
            "action": "read-owners-on",
            "reason": "owner-observations-required",
            "read_owners_on": missing_owner_ids,
            "tab_bound": bool(claim.get("tab_bound", False)),
        }
    owner_map = owner_map or {}
    owner_token = str(claim.get("tab_owner_token", ""))
    if not owner_token:
        raise BridgeError("Browser claim has no durable tab owner token")
    compatible_owner_tokens = {
        str(value).strip()
        for value in claim.get("compatible_tab_owner_tokens", [])
        if str(value).strip()
    }
    compatible_owner_tokens.add(owner_token)
    owned_pages = [
        page
        for page in chatgpt_pages
        if owner_map.get(page["page_id"]) in compatible_owner_tokens
    ]
    if len(owned_pages) > 1:
        return _hold("duplicate-owner", claim=claim, page_ids=[p["page_id"] for p in owned_pages])
    if owned_pages:
        page = owned_pages[0]
        if bootstrap:
            try:
                project_id, conversation_id = conversation_identity_from_url(page["url"])
            except BridgeError:
                project_id, conversation_id = "", ""
            if conversation_id:
                if not project_url_matches(project_id, expected_project_id):
                    return _hold("owned-tab-url-mismatch", claim=claim, page_id=page["page_id"], url=page["url"])
                bound = mark_browser_tab_bound(
                    repo, token=claim_token, observed_tab_owner_token=owner_token
                )
                return {
                    "action": "promote-ready",
                    "reason": "owned-bootstrap-created-conversation",
                    "page_id": page["page_id"],
                    "url": page["url"],
                    "conversation_id": conversation_id,
                    "canonical_url": canonical_bootstrap_url(expected_project_id),
                    "tab_owner_token": owner_token,
                    "tab_bound": bool(bound.get("tab_bound")),
                    "matching_page_count": 1,
                    "owner_selected_count": 1,
                }
            if page in matches["canonical_matches"]:
                bound = mark_browser_tab_bound(
                    repo, token=claim_token, observed_tab_owner_token=owner_token
                )
                return {
                    "action": "reuse-owned-tab",
                    "reason": "owned-bootstrap-home",
                    "page_id": page["page_id"],
                    "url": page["url"],
                    "canonical_url": canonical_bootstrap_url(expected_project_id),
                    "tab_owner_token": owner_token,
                    "tab_bound": bool(bound.get("tab_bound")),
                    "matching_page_count": len(matches["canonical_matches"]),
                    "owner_selected_count": 1,
                }
            return _hold("owned-tab-url-mismatch", claim=claim, page_id=page["page_id"], url=page["url"])

        if page not in matches["conversation_matches"]:
            return _hold("owned-tab-url-mismatch", claim=claim, page_id=page["page_id"], url=page["url"])
        observed_owner = owner_map.get(page["page_id"], "")
        if observed_owner != owner_token:
            return {
                "action": "replace-legacy-owner-token",
                "reason": "legacy-profile-alias-owner",
                "page_id": page["page_id"],
                "url": page["url"],
                "conversation_id": expected_conversation_id,
                "previous_tab_owner_token": observed_owner,
                "tab_owner_token": owner_token,
                "tab_bound": True,
                "matching_page_count": len(matches["conversation_matches"]),
                "owner_selected_count": 1,
            }
        return {
            "action": "reuse-owned-tab",
            "reason": "owned-conversation",
            "page_id": page["page_id"],
            "url": page["url"],
            "conversation_id": expected_conversation_id,
            "tab_owner_token": owner_token,
            "tab_bound": True,
            "matching_page_count": len(matches["conversation_matches"]),
            "owner_selected_count": 1,
        }

    if not bootstrap:
        conversation_matches = matches["conversation_matches"]
        if not conversation_matches:
            return _hold("owned-tab-not-listed", claim=claim)
        if len(conversation_matches) > 1:
            return _hold(
                "duplicate-conversation-url",
                claim=claim,
                page_ids=[page["page_id"] for page in conversation_matches],
            )
        page = conversation_matches[0]
        existing_owner = owner_map.get(page["page_id"], "")
        if existing_owner:
            return _hold("conversation-tab-owned-by-other", claim=claim, page_id=page["page_id"])
        return {
            "action": "set-owner-token",
            "reason": "unowned-conversation-tab",
            "page_id": page["page_id"],
            "url": page["url"],
            "conversation_id": expected_conversation_id,
            "tab_owner_token": owner_token,
            "tab_bound": True,
            "matching_page_count": 1,
            "owner_selected_count": 1,
        }

    if claim.get("tab_bound", False):
        return _hold("owned-tab-not-listed", claim=claim)
    canonical_matches = matches["canonical_matches"]
    if canonical_matches:
        eligible = []
        protected = []
        for page in canonical_matches:
            existing_owner = owner_map.get(page["page_id"], "")
            if not existing_owner:
                eligible.append((page, "set-owner-token", "empty", existing_owner))
                continue
            inspection = inspect_browser_tab_owner(
                repo,
                browser_profile=browser_profile,
                tab_owner_token=existing_owner,
            )
            status = inspection["status"]
            if status == "stale":
                eligible.append((page, "replace-stale-owner-token", status, existing_owner))
            else:
                protected.append({"page_id": page["page_id"], "owner_status": status})
        if not eligible:
            if len(protected) == 1:
                return _hold(
                    f"bootstrap-tab-owner-{protected[0]['owner_status']}",
                    claim=claim,
                    page_id=protected[0]["page_id"],
                    matching_page_count=len(canonical_matches),
                    owner_selected_count=0,
                )
            return _hold(
                "bootstrap-tabs-protected",
                claim=claim,
                protected_pages=protected,
                matching_page_count=len(canonical_matches),
                owner_selected_count=0,
            )
        page, action, status, existing_owner = min(
            eligible, key=lambda candidate: int(candidate[0]["page_id"])
        )
        return {
            "action": action,
            "reason": "canonical-bootstrap-home",
            "page_id": page["page_id"],
            "url": page["url"],
            "canonical_url": canonical_bootstrap_url(expected_project_id),
            "previous_owner_status": status,
            "previous_tab_owner_token": existing_owner,
            "tab_owner_token": owner_token,
            "tab_bound": False,
            "matching_page_count": len(canonical_matches),
            "owner_selected_count": 1,
        }
    if matches["noncanonical_project_home"]:
        return _hold(
            "noncanonical-bootstrap-tab",
            claim=claim,
            page_ids=[page["page_id"] for page in matches["noncanonical_project_home"]],
        )
    return {
        "action": "open-canonical-tab",
        "reason": "no-matching-bootstrap-tab",
        "canonical_url": canonical_bootstrap_url(expected_project_id),
        "tab_owner_token": owner_token,
        "tab_bound": False,
        "matching_page_count": 0,
        "owner_selected_count": 0,
    }


def prepare_bootstrap_tab(
    repo: Path,
    *,
    claim_token: str,
    thread_id: str,
    browser_profile: str,
    expected_project_id: str,
    matching_page_count: int,
    observed_page_url: str = "",
    observed_page_id: str = "",
    observed_tab_owner_token: str = "",
    pages: Optional[Sequence[Mapping[str, Any]]] = None,
    owners: Optional[Sequence[Mapping[str, Any]]] = None,
) -> Dict[str, Any]:
    """Compatibility wrapper around full-list bootstrap tab resolution."""
    if matching_page_count < 0:
        raise BridgeError("Bootstrap matching page count cannot be negative")
    if matching_page_count > 1 and pages is None:
        raise BridgeError("Multiple bootstrap matches require the complete page list")
    if pages is None:
        pages = (
            [{"page_id": observed_page_id, "url": observed_page_url}]
            if matching_page_count == 1
            else []
        )
        owners = (
            [{"page_id": observed_page_id, "owner_token": observed_tab_owner_token}]
            if matching_page_count == 1
            else []
        )
    computed = match_listed_pages(
        pages, expected_project_id=expected_project_id, bootstrap=True
    )
    if len(computed["canonical_matches"]) != matching_page_count:
        raise BridgeError("Caller matching-page-count disagrees with the full page list")
    return resolve_owned_tab(
        repo,
        claim_token=claim_token,
        thread_id=thread_id,
        browser_profile=browser_profile,
        expected_project_id=expected_project_id,
        pages=pages,
        owners=owners,
    )


def verify_bootstrap_tab(
    repo: Path,
    *,
    claim_token: str,
    thread_id: str,
    browser_profile: str,
    expected_project_id: str,
    observed_page_url: str,
    matching_page_count: int,
    observed_page_id: str,
    snapshot_page_id: str,
    observed_tab_owner_token: str,
    owner_selected_count: Optional[int] = None,
) -> Dict[str, Any]:
    """Verify the uniquely owned Project/home tab before the first Send."""
    if matching_page_count < 1:
        raise BridgeError("Bootstrap destination must match at least one open browser tab")
    if owner_selected_count is None:
        if matching_page_count != 1:
            raise BridgeError(
                "Multiple bootstrap URLs require resolver proof of one selected owner"
            )
        owner_selected_count = 1
    if owner_selected_count != 1:
        raise BridgeError("Bootstrap identity must resolve to exactly one owned browser tab")
    observed_page_id = observed_page_id.strip()
    snapshot_page_id = snapshot_page_id.strip()
    if not observed_page_id or observed_page_id != snapshot_page_id:
        raise BridgeError("The fresh bootstrap snapshot came from a different or missing pageId")
    observed_project_segment = bootstrap_destination_from_url(
        observed_page_url.strip(), expected_project_id
    )
    claim = assert_browser_lease_held(repo, token=claim_token)
    expected_scope = "project" if expected_project_id.strip() else "profile"
    if not claim.get("bootstrap", False) or claim.get("scope") != expected_scope:
        raise BridgeError("This browser operation requires an active bootstrap claim")
    if claim.get("thread_id") != thread_id.strip():
        raise BridgeError("Bootstrap claim belongs to a different Bridge Thread")
    if normalize_browser_profile(str(claim.get("browser_profile", ""))) != normalize_browser_profile(
        browser_profile
    ):
        raise BridgeError("Bootstrap claim profile does not match the observed profile")
    if claim.get("expected_remote_project_id", "") != expected_project_id.strip():
        raise BridgeError("Bootstrap claim Project does not match the destination")
    owner_token = str(claim.get("tab_owner_token", ""))
    if not owner_token or observed_tab_owner_token.strip() != owner_token:
        raise BridgeError("The new-chat tab is not owned by this bootstrap claim")
    return {
        "claim": claim,
        "observed_project_segment": observed_project_segment,
        "matching_page_count": matching_page_count,
        "owner_selected_count": owner_selected_count,
        "observed_page_id": observed_page_id,
        "snapshot_page_id": snapshot_page_id,
        "tab_owner_token": owner_token,
    }


def promote_bootstrap_tab(
    repo: Path,
    *,
    claim_token: str,
    thread_id: str,
    browser_profile: str,
    expected_project_id: str,
    observed_page_url: str,
    matching_page_count: int,
    observed_page_id: str,
    snapshot_page_id: str,
    observed_tab_owner_token: str,
) -> Dict[str, Any]:
    """Bind the first post-Send conversation URL to its bootstrap claim."""
    if matching_page_count != 1:
        raise BridgeError("Created conversation must match exactly one open browser tab")
    observed_page_id = observed_page_id.strip()
    if not observed_page_id or observed_page_id != snapshot_page_id.strip():
        raise BridgeError("The post-Send snapshot came from a different or missing pageId")
    observed_project_id, conversation_id = conversation_identity_from_url(
        observed_page_url.strip()
    )
    if not project_url_matches(observed_project_id, expected_project_id.strip()):
        raise BridgeError("Created conversation belongs to a different ChatGPT Project")
    promoted = promote_browser_bootstrap(
        repo,
        token=claim_token,
        thread_id=thread_id,
        conversation_id=conversation_id,
        expected_remote_project_id=expected_project_id,
        browser_profile=browser_profile,
        observed_tab_owner_token=observed_tab_owner_token,
    )
    return {
        "claim": promoted,
        "observed_project_id": observed_project_id,
        "conversation_id": conversation_id,
        "observed_page_url": observed_page_url.strip(),
        "observed_page_id": observed_page_id,
        "snapshot_page_id": snapshot_page_id.strip(),
        "tab_owner_token": observed_tab_owner_token.strip(),
    }


def verify_claimed_tab(
    repo: Path,
    *,
    claim_token: str,
    thread_id: str,
    browser_profile: str,
    expected_project_id: str,
    expected_conversation_id: str,
    observed_page_url: str,
    matching_page_count: int,
    observed_page_id: str,
    snapshot_page_id: str,
    observed_tab_owner_token: str,
    owner_selected_count: Optional[int] = None,
) -> Dict[str, Any]:
    expected_conversation_id = expected_conversation_id.strip()
    if matching_page_count < 1:
        raise BridgeError(
            "Exact Project/conversation identity must match at least one open browser tab"
        )
    if owner_selected_count is None:
        if matching_page_count != 1:
            raise BridgeError(
                "Multiple conversation URLs require resolver proof of one selected owner"
            )
        owner_selected_count = 1
    if owner_selected_count != 1:
        raise BridgeError("Conversation identity must resolve to exactly one owned browser tab")
    observed_page_id = observed_page_id.strip()
    snapshot_page_id = snapshot_page_id.strip()
    if not expected_conversation_id:
        raise BridgeError("Expected conversation id is required")
    if not observed_page_id or observed_page_id != snapshot_page_id:
        raise BridgeError("The fresh snapshot came from a different or missing MCP pageId")

    url_project_id, url_conversation_id = conversation_identity_from_url(
        observed_page_url.strip()
    )
    if url_conversation_id != expected_conversation_id:
        raise BridgeError("The current page URL identifies a different ChatGPT conversation")
    if not project_url_matches(url_project_id, expected_project_id.strip()):
        raise BridgeError("The current page URL identifies a different ChatGPT Project")

    claim = assert_browser_lease_held(repo, token=claim_token)
    if claim.get("scope") != "conversation":
        raise BridgeError("This browser operation requires a conversation-scoped claim")
    if claim.get("thread_id") != thread_id.strip():
        raise BridgeError(
            f"Browser claim belongs to thread {claim.get('thread_id') or '<none>'!r}, "
            f"not {thread_id.strip()!r}"
        )
    if normalize_browser_profile(str(claim.get("browser_profile", ""))) != normalize_browser_profile(
        browser_profile
    ):
        raise BridgeError("Browser claim profile does not match the observed profile")
    if claim.get("expected_conversation_id") != expected_conversation_id:
        raise BridgeError("Browser claim conversation does not match the observed page")
    if claim.get("expected_remote_project_id", "") != expected_project_id.strip():
        raise BridgeError("Browser claim Project does not match the observed page")
    owner_token = str(claim.get("tab_owner_token", ""))
    if not owner_token or observed_tab_owner_token.strip() != owner_token:
        raise BridgeError("The current tab is not owned by this Bridge Thread")
    return {
        "claim": claim,
        "url_project_id": url_project_id,
        "url_conversation_id": url_conversation_id,
        "matching_page_count": matching_page_count,
        "owner_selected_count": owner_selected_count,
        "observed_page_id": observed_page_id,
        "snapshot_page_id": snapshot_page_id,
        "tab_owner_token": owner_token,
    }
