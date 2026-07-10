---
name: gpt-pro-question-window
description: Bridge Codex to a signed-in ChatGPT/GPT Pro conversation, including scoped file upload, conversation reuse, raw answer capture, and later Codex verification. Use for normal GPT Pro questions and as the browser/persistence foundation for other Codex Pro Bridge skills; do not use for local tasks that need no external reasoning.
---

# GPT Pro Question Window

Use this skill as the browser and persistence adapter for Codex Pro Bridge.

Before creating or resuming bridge state, read [references/bridge_protocol.md](references/bridge_protocol.md). It is the single source of truth for IDs, events, storage, invariants, and CLI commands.

## Required flow

1. Identify the exact question, desired output, and one `bridge-thread-id`.
2. Reuse the GPT Pro session only when its local metadata points to the intended web conversation and the same bridge thread. Otherwise create a new task-scoped conversation.
3. Open ChatGPT in the user's signed-in Chrome session. Ask the user to handle login, passwords, 2FA, CAPTCHA, rate limits, or account-security prompts.
4. Upload a focused zip when evidence is needed. Never replace a failed upload with a full repository paste unless the user explicitly approves that fallback.
5. Verify the attachment chip, selected conversation, and visible model state before sending. Do not claim an exact model when the UI does not establish it.
6. Wait until the response finishes, then immediately capture the prompt, bundle digest, and full raw answer with `scripts/save_bridge_turn.py`.
7. Re-open local evidence, verify the answer, and record the result separately with `scripts/record_codex_verdict.py`.
8. Report the saved turn and verdict paths, useful conclusions, rejected claims, and next action.

Completion criterion: the raw exchange and Codex verdict are separate immutable artifacts on the same thread, and every acted-on GPT Pro claim has a local verdict.

## Normal-question prompt

For a normal question, read and use [references/question_window_prompt.md](references/question_window_prompt.md). Specialized review skills provide their own prompt.

## Chrome upload

Use Chrome's file chooser before Computer Use:

1. Before the first browser round, verify that the Codex Chrome extension is installed and enabled. In this environment, use a US-region network node while downloading it from the Chrome Web Store.
2. Open `chrome://extensions/`, open the extension's **Details**, and verify **Allow access to file URLs** is enabled.
3. Build the zip locally and keep its output path absolute for the browser call.
4. Start `waitForEvent("filechooser")` before clicking ChatGPT's visible upload control.
5. Call `chooser.setFiles([absolute_path])`.
6. Verify the filename or attachment chip before submitting.
7. Remove the attachment and confirm an empty composer after a dry run.

If the extension is missing or the permission is disabled, stop and fix that prerequisite before retrying. Use Computer Use only when Chrome still cannot control the native or graphical UI boundary after the extension and permission checks pass.

Treat a long document-reading state as a bundle-shape problem: regenerate a smaller package or at most two or three focused attachments. Do not paste the full bundle as a workaround.

## Browser pacing

- Prefer one conversation. Use two or three only for independent tasks, never the same bridge thread concurrently.
- Wait for visible completion between upload, submit, streaming, and copy operations.
- Inspect after failure; avoid rapid retries, scraping, or burst submission.
- Stop for service or account protections rather than attempting to bypass them.

## Non-negotiable checks

- Keep uploaded evidence inside the user-approved scope.
- Save the answer before using it.
- Verify locally before editing code or trusting a result.
- Never move an existing local session to another thread or web URL.
- Never overwrite a saved bundle, turn, snapshot, or verdict.
