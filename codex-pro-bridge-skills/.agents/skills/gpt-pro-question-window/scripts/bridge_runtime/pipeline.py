"""Mechanical round runner. Browser/attempt/ledger gates are reused verbatim."""
from __future__ import annotations

import json
import os
from pathlib import Path
import subprocess
import sys
import time

from bridge_attempts import (active_attempt, browser_wait_script, digest, mark_send_started,
                             prepare_attempt, read_attempt, record_submission)
from bridge_store import (BridgeError, DEFAULT_BROWSER_PROFILE, TAB_OWNER_STORAGE_KEY,
                          acquire_browser_lease, assert_browser_lease_held, file_lock,
                          file_sha256, now_iso, parse_metadata, release_browser_lease,
                          default_gpt_session_id, bridge_root)
from browser_host import cleanup_staged_file, staging_windows_root
from browser_identity import (conversation_identity_from_url, project_url_matches,
                              resolve_owned_tab, promote_bootstrap_tab)
from project_store import BridgeProjectStore
from .browser import Browser, unique_uid
from .jobs import validate_inputs, write_json
from .rpc import Client

SCRIPTS = Path(__file__).resolve().parents[1]
SKILLS = SCRIPTS.parents[1]


def run_helper(script, args, repo, *, json_output=True):
    result = subprocess.run([sys.executable, str(script), *map(str, args)], cwd=repo,
        env={**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"},
        capture_output=True, text=True, encoding="utf-8", check=False, timeout=180)
    if result.returncode:
        raise BridgeError(f"{script.name} failed: {(result.stderr or result.stdout)[-6000:]}")
    return json.loads(result.stdout) if json_output else result.stdout.strip()


def resolve(browser, repo, h, claim, *, allow_prepare):
    for _ in range(4):
        pages = browser.pages()
        owners = browser.owners(pages)
        result = resolve_owned_tab(repo, claim_token=claim["token"], thread_id=h["bridge_thread_id"],
            browser_profile=claim["browser_profile"], expected_project_id=h.get("remote_project_id", ""),
            pages=pages, owners=owners)
        action = result["action"]
        if action in {"reuse-owned-tab", "promote-ready"}:
            browser.page_id, browser.url = result["page_id"], result["url"]
            browser.owner = result["tab_owner_token"]
            return result, pages, owners
        if allow_prepare and action == "open-canonical-tab":
            browser.client.call("new_page", url=result["canonical_url"])
            continue
        if allow_prepare and action in {"set-owner-token", "replace-stale-owner-token", "replace-legacy-owner-token"}:
            browser.page_id = result["page_id"]
            # Compare-and-set against the exact observation; never steal a tab.
            browser.evaluate("() => { const key = " + json.dumps(TAB_OWNER_STORAGE_KEY) +
                "; if (location.href !== " + json.dumps(result["url"]) +
                " || (sessionStorage.getItem(key) || '') !== " +
                json.dumps(result.get("previous_tab_owner_token", "")) +
                ") throw Error('Owner changed'); sessionStorage.setItem(key, " +
                json.dumps(result["tab_owner_token"]) + "); return true; }")
            continue
        raise BridgeError(f"Tab resolution HOLD: {result}")
    raise BridgeError("Tab resolution did not stabilize within its action budget")


def submission_candidate(observation, attempt):
    """Find one user message after the boundary, without trusting rendered text."""
    if observation["owner"] != attempt["tab_owner_token"]:
        raise BridgeError("Submitted page owner mismatch")
    project, conversation = conversation_identity_from_url(observation["url"])
    if not project_url_matches(project, attempt["project_id"]):
        raise BridgeError("Submitted conversation Project mismatch")
    if attempt["conversation_id"] and attempt["conversation_id"] != conversation:
        raise BridgeError("Submitted conversation changed")
    messages = observation["messages"]
    boundary = attempt["pre_submit_boundary"]
    start = 0
    if boundary != "new-conversation":
        found = [i for i, msg in enumerate(messages) if msg["id"] == boundary]
        if len(found) != 1:
            raise BridgeError("Pre-Send boundary is not uniquely visible")
        start = found[0] + 1
    users = [msg for msg in messages[start:] if msg["role"] == "user"]
    if len(users) != 1:
        raise BridgeError("Exact submitted prompt is not uniquely visible; no resend")
    if not users[0]["id"]:
        raise BridgeError("Submitted user turn lacks a stable ID")
    return users[0]


def observed_submission(observation, attempt):
    user = submission_candidate(observation, attempt)
    if user["text"].strip() != attempt["prompt"].strip():
        raise BridgeError("Exact submitted prompt is not uniquely visible; no resend")
    return user["id"]


def verify_copied_submission(browser, observation, attempt, output, config):
    """Rendered Markdown is lossy; Copy message must match the frozen raw text."""
    from capture_copied_reply import read_windows_clipboard
    from bridge_store import atomic_write_text
    user = submission_candidate(observation, attempt)
    browser.url = observation["url"]
    with file_lock(Path(config["state_dir"]) / "clipboard.lock"):
        browser.snapshot()
        before = read_windows_clipboard()
        browser.assert_identity()
        browser.evaluate("() => { const id = " + json.dumps(user["id"]) + ";" + r'''
            const nodes = [...document.querySelectorAll('[data-message-author-role="user"][data-message-id]')]
              .filter(n => n.getAttribute('data-message-id') === id);
            if (nodes.length !== 1) throw Error('User turn identity ambiguous');
            const buttons = [...(nodes[0].closest('[data-testid^="conversation-turn-"]') || nodes[0])
              .querySelectorAll('button[data-testid="copy-turn-action-button"]')]
              .filter(n => n.getClientRects().length && !n.disabled);
            if (buttons.length !== 1) throw Error('Copy message unavailable');
            buttons[0].click(); return true;
        }''')
        deadline = time.monotonic() + 10
        while True:
            copied = read_windows_clipboard()
            if copied != before:
                break
            if time.monotonic() >= deadline:
                raise BridgeError("User-message clipboard change not proven; no resend")
            time.sleep(0.25)
        browser.assert_identity()
        fresh = submission_candidate(browser.messages(), attempt)
        if fresh["id"] != user["id"] or copied.strip() != attempt["prompt"].strip():
            raise BridgeError("Copied user message differs from frozen prompt; no resend")
        path = output / "copied-user-prompt.md"
        if path.exists() and path.read_text(encoding="utf-8") != copied:
            raise BridgeError("Copied user prompt artifact drift")
        atomic_write_text(path, copied)
        write_json(output / "submission-copy.json", {"remote_turn_id": user["id"],
            "conversation_url": browser.url, "tab_owner_token": browser.owner,
            "prompt_sha256": attempt["prompt_sha256"], "copy_sha256": file_sha256(path)})
        return user["id"]


def identity_args(browser, claim, resolved, pages, owners):
    return ["--browser-lease-token", claim["token"], "--browser-profile", claim["browser_profile"],
        "--observed-page-url", browser.url, "--matching-page-count", resolved["matching_page_count"],
        "--pages-json", json.dumps(pages), "--owners-json", json.dumps(owners),
        "--observed-page-id", browser.page_id, "--snapshot-page-id", browser.page_id,
        "--observed-tab-owner-token", browser.owner]


def cleanup(job):
    staging = job.data.get("preparation", {}).get("staging")
    if staging and not job.data.get("staging_cleaned"):
        path = Path(staging["staged_execution_path"])
        if not path.exists() and not path.is_symlink():
            # A crash can occur after deletion but before its receipt. Observe
            # absence explicitly; do not invent a successful deletion receipt.
            job.update(staging_cleaned={"status": "already-absent", "cleaned": False,
                       "path": str(path), "expected_sha256": staging["source_sha256"]})
            return
        receipt = cleanup_staged_file(staging["staged_execution_path"], expected_sha256=staging["source_sha256"])
        job.update(staging_cleaned=receipt)


def capture(browser, attempt, output, config):
    from capture_copied_reply import read_windows_clipboard, markdown_metrics
    # The clipboard is a host-wide shared resource even across separate tabs.
    with file_lock(Path(config["state_dir"]) / "clipboard.lock"):
        event = browser.evaluate(browser_wait_script(attempt, browser.url)["function"])
        if event["status"] != "ready-for-capture":
            raise BridgeError(f"Answer changed before capture: {event}")
        assistant_id = event["assistant_turn_id"]
        browser.snapshot()
        metrics = browser.evaluate("() => { const id = " + json.dumps(assistant_id) + "; " + r'''
            const nodes = [...document.querySelectorAll('[data-message-id]')].filter(n => n.getAttribute('data-message-id') === id);
            if (nodes.length !== 1) throw Error('Answer identity ambiguous');
            const node = nodes[0], body = node.querySelector('.markdown');
            const turn = node.closest('[data-testid^="conversation-turn-"]');
            const button = turn?.querySelector('button[data-testid="copy-turn-action-button"],button[aria-label="Copy response"],button[aria-label="复制回复"]');
            if (!body || !button || button.disabled || !button.getClientRects().length) throw Error('Copy reply unavailable');
            const math = [...body.querySelectorAll('[role="math"]')];
            const display = math.filter(n => n.closest('.katex-display')).length;
            return {headings: body.querySelectorAll('h1,h2,h3,h4,h5,h6').length,
              display_math: display, inline_math: math.length - display,
              tables: body.querySelectorAll('table').length, code_blocks: body.querySelectorAll('pre').length,
              links: body.querySelectorAll('a').length};
        }''')
        try:
            before = read_windows_clipboard()
        except RuntimeError:
            before = ""
        browser.assert_identity()
        browser.evaluate("() => { const id = " + json.dumps(assistant_id) + "; " + r'''
            const nodes = [...document.querySelectorAll('[data-message-id]')].filter(n => n.getAttribute('data-message-id') === id);
            if (nodes.length !== 1) throw Error('Answer identity changed');
            const button = nodes[0].closest('[data-testid^="conversation-turn-"]')?.querySelector(
                'button[data-testid="copy-turn-action-button"],button[aria-label="Copy response"],button[aria-label="复制回复"]');
            if (!button || button.disabled || !button.getClientRects().length) throw Error('Copy reply unavailable');
            button.click(); return true;
        }''')
        deadline = time.monotonic() + 10
        while True:
            text = read_windows_clipboard()
            if text != before:
                break
            if time.monotonic() >= deadline:
                raise BridgeError("Clipboard change not proven; refusing stale capture")
            time.sleep(0.25)
        browser.assert_identity()
        actual = markdown_metrics(text)
        if any(actual[key] != expected for key, expected in metrics.items()):
            raise BridgeError("Copied Markdown structure differs from pinned answer")
        from bridge_store import atomic_write_text
        if output.exists() and output.read_text(encoding="utf-8") != text:
            raise BridgeError("Immutable answer already exists with different contents")
        atomic_write_text(output, text)
        return {"answer_path": str(output), "answer_sha256": file_sha256(output),
                "assistant_turn_id": assistant_id, "completed_at": event["observed_at"]}


def execute(job, config):
    started = time.monotonic()
    h, request = validate_inputs(job.data["handoff_path"], job.data["handoff_sha256"], config)
    repo, thread = Path(h["repo"]), h["bridge_thread_id"]
    attempt = read_attempt(repo, thread, job.data["attempt_id"]) if job.data.get("attempt_id") else active_attempt(repo, thread)
    recovery = attempt is not None
    output = Path(h["expected_output_dir"]) / ("runtime-" + job.data["job_id"][:16])
    upload_resume = (not recovery and job.data["stage"] == "upload-started"
                     and (output / "upload-result.transport.json").is_file())
    connection_resume = (not recovery and job.data["stage"] == "prepared-materials"
                         and job.data.get("browser_phase") in (None, "acquiring-claim", "connecting-browser"))
    if recovery and attempt["prompt_sha256"] != job.data.get("preparation", {}).get("prompt_sha256"):
        raise BridgeError("Unresolved attempt is not owned by this job")
    if not recovery and (h["mode"] == "recover-only" or
                         (job.data["stage"] != "queued" and not connection_resume and not upload_resume)):
        raise BridgeError("Interrupted pre-Send work requires inspection; upload/draft actions will not be replayed")
    if recovery and attempt["state"] == "prepared":
        raise BridgeError("Interrupted prepared attempt requires fresh preflight; no automatic Send")
    if recovery and attempt["state"] == "captured":
        finish(job, repo, attempt)
        return
    output = Path(h["expected_output_dir"]) / ("runtime-" + job.data["job_id"][:16])
    output.mkdir(parents=True, exist_ok=True)
    project_id = h.get("remote_project_id", "")
    store = BridgeProjectStore(repo)
    binding = store.load_binding(h["bridge_project_id"]) if project_id else {}
    if project_id and (binding.get("status") != "active" or binding.get("remote_project_id") != project_id):
        raise BridgeError("Fast path requires verified Project binding; run Project verification before dispatch")
    if project_id:
        report = store.verify(h["bridge_project_id"])
        if report["project_status"] != "active" or store.project_for_thread(thread) != h["bridge_project_id"]:
            raise BridgeError("Thread must belong to the active Project")
        if report["unsynced_source_count"] or not report["inventory_verified"]:
            raise BridgeError("Project Sources require reconciliation before dispatch")
    if not recovery and not connection_resume and not upload_resume:
        job.update(state="running", stage="preparing")
        notes_args = ["--repo", repo, "--bridge-thread-id", thread, "--goal", request["goal"],
                      "--summary-file", request["notes"], "--gpt-pro-question", request["question"]]
        if h.get("bridge_project_id"):
            notes_args += ["--bridge-project-id", h["bridge_project_id"]]
        from bridge_store import default_codex_session_id
        codex_meta = parse_metadata(bridge_root(repo) / "codex-sessions" / default_codex_session_id(thread) / "session.md")
        if not codex_meta:
            run_helper(SKILLS / "bundle-algorithm-context/scripts/prepare_codex_session_notes.py", notes_args,
                       repo, json_output=False)
        elif codex_meta.get("bridge_thread_id") != thread:
            raise BridgeError("Codex session metadata belongs to another thread")
        args = ["--request", h["request_file"]]
        if h["attachment_policy"] == "bundle":
            args += ["--stage", "--out", output / "bundle.zip"]
        prep = run_helper(SCRIPTS / "prepare_review.py", args, repo)
        if not prep.get("ready"):
            raise BridgeError("Preparation failed")
        job.update(preparation=prep, stage="prepared-materials")
    prep = job.data["preparation"]
    prompt = Path(prep["prompt_file"]).read_text(encoding="utf-8")
    if digest(prompt) != prep["prompt_sha256"]:
        raise BridgeError("Prepared prompt digest drift")
    meta = parse_metadata(bridge_root(repo) / "gpt-pro-sessions" / default_gpt_session_id(thread) / "session.md")
    conversation_id = attempt["conversation_id"] if recovery else meta.get("expected_conversation_id", "")
    if not conversation_id and meta.get("web_url"):
        _, conversation_id = conversation_identity_from_url(meta["web_url"])
    profile = config.get("browser_profile", DEFAULT_BROWSER_PROFILE)
    holder = "bridge-mcp-" + job.data["job_id"][:24]
    # Reuse a surviving short claim when the process died. Otherwise reacquire
    # through the canonical registry, which restores the durable owner token.
    claim = None
    if job.data.get("claim_token"):
        try:
            claim = assert_browser_lease_held(repo, token=job.data["claim_token"])
        except BridgeError:
            pass
    if not claim:
        # This phase precedes every browser action. Contention can be resolved
        # externally without forcing a fresh bundle or a new job on resume.
        job.update(browser_phase="acquiring-claim")
        claim = acquire_browser_lease(repo, holder=holder, thread_id=thread,
            expected_conversation_id=conversation_id, expected_remote_project_id=project_id,
            browser_profile=profile, bootstrap=not bool(conversation_id))
    job.update(state="running", claim_token=claim["token"], browser_phase="connecting-browser")
    env = {**os.environ, **config.get("browser_env", {})}
    with (job.directory / "browser.log").open("ab") as log:
        client = None
        try:
            if config.get("browser_transport", "stdio") == "persistent":
                from .connection_service import SharedClient
                client = SharedClient(config)
            else:
                client = Client(config["browser_command"], env=env, stderr=log,
                                timeout=config.get("browser_tool_timeout_seconds", 90),
                                connect_timeout=config.get("browser_connect_timeout_seconds", 300))
            browser = Browser(client, config["ui"])
            resolved, pages, owners = resolve(browser, repo, h, claim, allow_prepare=not recovery and not upload_resume)
            job.update(browser_phase="connected")
            if not recovery:
                if resolved["action"] != "reuse-owned-tab":
                    raise BridgeError("Existing bootstrap conversation requires recovery")
                if browser.composer_text() or (not upload_resume and browser.attachment_count()):
                    raise BridgeError("Existing draft or attachments block dispatch")
                if upload_resume:
                    browser.recover_upload(prep["staging"], output / "upload-plan.json",
                                           output / "upload-result.json", write_json)
                labels = browser.read_labels(("workspace", "account")) if project_id else {}
                if project_id and (labels["workspace"] != binding["workspace"] or labels["account"] != binding["account_label"]):
                    raise BridgeError("Observed account/workspace differs from Project binding")
                control = browser.adjust_controls(h)
                control_path = output / "model-controls.json"
                write_json(control_path, control)
                if prep.get("staging") and not upload_resume:
                    browser.upload(prep["staging"], output / "upload-plan.json", output / "upload-result.json",
                        write_json, lambda: job.update(stage="upload-started"))
                    job.update(stage="uploaded")
                observed = browser.messages()
                boundary = observed["messages"][-1]["id"] if observed["messages"] else "new-conversation"
                if claim.get("bootstrap") and observed["messages"]:
                    raise BridgeError("Bootstrap page unexpectedly contains conversation messages")
                browser.fill_prompt(prompt)
                if browser.attachment_count() != (1 if prep.get("staging") else 0):
                    raise BridgeError("Unexpected composer attachment count")
                resolved, pages, owners = resolve(browser, repo, h, claim, allow_prepare=False)
                browser.snapshot()
                args = ["--repo", repo, "--bridge-thread-id", thread,
                    *identity_args(browser, claim, resolved, pages, owners),
                    "--requested-model", h["requested_model"], "--selected-ui-label", control["selected_model"],
                    "--model-selection-kind", h["model_selection_kind"],
                    "--requested-thinking-intensity", h["requested_thinking_intensity"],
                    "--selected-thinking-intensity", control["selected_thinking_intensity"],
                    "--model-control-receipt", control_path, "--pre-submit-boundary", boundary,
                    "--prompt-sha256", digest(prompt), "--mcp-host-os", "windows",
                    "--browser-host-os", "windows", "--mcp-temp-root", staging_windows_root()]
                if project_id:
                    args += ["--expected-project-id", project_id, "--observed-project-id", project_id,
                        "--expected-workspace", binding["workspace"], "--observed-workspace", labels["workspace"],
                        "--expected-account-label", binding["account_label"], "--observed-account-label", labels["account"],
                        "--binding-status", binding["status"]]
                if claim.get("bootstrap"):
                    args += ["--conversation-bootstrap"]
                else:
                    args += ["--expected-conversation-id", conversation_id, "--observed-conversation-id", conversation_id]
                if prep.get("staging"):
                    args += ["--source-bundle", prep["bundle"], "--bundle", prep["staging"]["staged_execution_path"],
                        "--attachment-name", prep["staging"]["attachment_name"], "--upload-control", "devtools-direct-menu-upload",
                        "--upload-action-plan", output / "upload-plan.json", "--upload-result", output / "upload-result.json"]
                preflight = run_helper(SCRIPTS / "check_browser_preflight.py", args, repo)
                write_json(output / "preflight.json", preflight)
                attempt = prepare_attempt(repo, thread, prompt, preflight, h.get("business_deadline"))
                job.update(attempt_id=attempt["attempt_id"], stage="preflight")
                send_uid = unique_uid(browser.snapshot(), ("button",), config["ui"]["send_labels"])
                browser.assert_identity()
                if browser.composer_text() != prompt.strip():
                    raise BridgeError("Prompt changed before Send")
                attempt = mark_send_started(repo, thread, attempt["attempt_id"])
                job.update(stage="send-started")
                client.call("click", pageId=int(browser.page_id), uid=send_uid)
            if attempt["state"] in {"send-started", "failed"}:
                deadline = time.monotonic() + 30
                while True:
                    try:
                        observed = browser.messages(check_identity=False)
                        submission_candidate(observed, attempt)
                        break
                    except BridgeError:
                        if time.monotonic() >= deadline:
                            raise
                        time.sleep(0.5)
                if submission_candidate(observed, attempt)["text"].strip() == attempt["prompt"].strip():
                    user_id = observed_submission(observed, attempt)
                else:
                    user_id = verify_copied_submission(browser, observed, attempt, output, config)
                browser.url = observed["url"]
                browser.assert_identity()
                if claim.get("bootstrap"):
                    browser.snapshot()
                    new_pages = browser.pages()
                    count = sum(p["url"] == browser.url for p in new_pages)
                    promoted = promote_bootstrap_tab(repo, claim_token=claim["token"], thread_id=thread,
                        browser_profile=profile, expected_project_id=project_id, observed_page_url=browser.url,
                        matching_page_count=count, observed_page_id=str(browser.page_id),
                        snapshot_page_id=str(browser.page_id), observed_tab_owner_token=browser.owner)
                    claim = promoted["claim"]
                attempt = record_submission(repo, thread, attempt["attempt_id"], conversation_url=browser.url,
                    owner=browser.owner, prompt_sha256=digest(prompt), boundary=attempt["pre_submit_boundary"],
                    remote_turn_id=user_id, after_boundary="yes", submitted_at=now_iso())
            job.update(attempt_id=attempt["attempt_id"], stage="waiting", state="waiting",
                conversation_url=attempt["conversation_url"], remote_turn_id=attempt["remote_turn_id"],
                timings={"prepare_to_wait_seconds": round(time.monotonic() - started, 3)})
            release_browser_lease(repo, token=claim["token"])
            job.update(claim_token="")
            cleanup(job)
            while True:
                event = browser.evaluate(browser_wait_script(attempt, browser.url)["function"])
                if event["status"] == "pending":
                    continue
                if event["status"] != "ready-for-capture":
                    raise BridgeError(f"Pinned-turn wait needs recovery: {event}")
                break
            claim = acquire_browser_lease(repo, holder=holder, thread_id=thread,
                expected_conversation_id=attempt["conversation_id"], expected_remote_project_id=project_id,
                browser_profile=profile)
            job.update(claim_token=claim["token"], state="running", stage="capturing")
            resolved, pages, owners = resolve(browser, repo, h, claim, allow_prepare=False)
            receipt = job.data.get("capture")
            if not receipt:
                receipt = capture(browser, attempt, output / "answer.md", config)
                job.update(capture=receipt)
            if file_sha256(Path(receipt["answer_path"])) != receipt["answer_sha256"]:
                raise BridgeError("Captured answer digest drift")
            browser.snapshot()
            args = ["--repo", repo, "--bridge-thread-id", thread, "--attempt-id", attempt["attempt_id"],
                "--capture-route", "browser-fallback", "--answer-format", "copied-markdown",
                "--expected-conversation-id", attempt["conversation_id"], "--web-url", browser.url,
                "--remote-turn-id", attempt["remote_turn_id"], "--prompt-file", prep["prompt_file"],
                "--answer-file", receipt["answer_path"], "--response-completed-at", receipt["completed_at"],
                "--submitted-at", attempt["submitted_at"], *identity_args(browser, claim, resolved, pages, owners)]
            preflight = attempt["preflight"]
            for key in ("requested_model", "selected_ui_label", "model_selection_kind", "requested_thinking_intensity",
                        "selected_thinking_intensity", "attachment_name", "upload_control"):
                if preflight.get(key):
                    args += ["--" + key.replace("_", "-"), preflight[key]]
            if prep.get("bundle"):
                args += ["--bundle", prep["bundle"]]
            if project_id:
                labels = browser.read_labels(("workspace", "account"))
                if labels["workspace"] != binding["workspace"] or labels["account"] != binding["account_label"]:
                    raise BridgeError("Capture account/workspace differs from the binding")
                args += ["--bridge-project-id", h["bridge_project_id"], "--remote-project-id", project_id,
                         "--observed-workspace", labels["workspace"], "--observed-account-label", labels["account"]]
            else:
                args += ["--standalone"]
            run_helper(SCRIPTS / "save_bridge_turn.py", args, repo, json_output=False)
            finish(job, repo, read_attempt(repo, thread, attempt["attempt_id"]))
        finally:
            # Preserve pre-Send bootstrap/uncertain owner state for recovery.
            # Once promoted, releasing a live claim does not erase its binding.
            if not claim.get("bootstrap"):
                release_browser_lease(repo, token=claim["token"])
                job.update(claim_token="")
            if client is not None:
                client.close()


def finish(job, repo, attempt):
    receipt = job.data.get("capture")
    if not receipt or attempt["state"] != "captured":
        raise BridgeError("Completion requires saved raw answer and canonical captured attempt")
    turn = repo / attempt["capture"]["path"]
    if file_sha256(turn) != attempt["capture"]["sha256"]:
        raise BridgeError("Canonical turn digest drift")
    job.update(state="complete", stage="captured", error="", result={
        **receipt, "turn_path": str(turn), "turn_sha256": file_sha256(turn),
        "attempt_id": attempt["attempt_id"], "remote_turn_id": attempt["remote_turn_id"],
        "conversation_url": attempt["conversation_url"], "capture_route": "browser-fallback",
        "answer_format": "copied-markdown"})
