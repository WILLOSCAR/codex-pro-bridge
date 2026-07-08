---
name: gpt-pro-question-window
description: Base Codex-to-GPT-Pro bridge. Use when the user wants Codex to open or reuse a signed-in ChatGPT/GPT Pro conversation, ask a normal question, optionally upload/paste a context bundle, and save the answer. Do not use for simple local coding tasks that do not need GPT Pro.
---

# GPT Pro Question Window

This is the foundation skill for all Codex → GPT Pro workflows.

Use it to ask GPT Pro a normal question or to submit a prepared prompt/bundle. Keep the workflow auditable: one bridge thread maps to one task, one local GPT Pro session maps to one GPT Pro web conversation, and each follow-up in that conversation becomes one saved turn file.

## Tool choice

1. Prefer `@Chrome` / Codex Chrome integration for ChatGPT because ChatGPT usually requires a signed-in browser state.
2. Use `@Computer` only when Chrome automation is insufficient, such as when a file picker, modal, drag-and-drop upload, or graphical UI needs visual operation.
3. Use the in-app browser only for localhost, file previews, or public pages that do not require sign-in. Do not rely on it for ChatGPT login state.
4. If ChatGPT is not signed in, stop and ask the user to sign in manually. Never type passwords, recovery codes, or 2FA codes.

## Browser pacing

Operate the ChatGPT web UI conservatively. The goal is reliability and respect for the service, not high-throughput automation.

- Prefer one GPT Pro conversation. Use two or three when useful for independent tasks, but never exceed three concurrent GPT Pro conversations.
- Add small, natural delays between UI actions, especially clicking, pasting, uploading, and submitting. Vary waits rather than firing back-to-back actions.
- Wait for visible UI completion before the next action: upload finished, message submitted, answer stopped streaming, copy target available.
- Avoid rapid retries. If an action fails, pause, inspect the page, and retry only once or twice with a longer wait.
- Do not run bulk extraction, scraping, or burst-style simultaneous prompt submission through the web UI.
- If ChatGPT shows a CAPTCHA, abuse warning, rate-limit message, unusual login prompt, or account/security interstitial, stop and ask the user to handle it manually.

## Inputs to collect

Before opening GPT Pro, identify:

- The exact user question.
- Whether a context file/bundle should be uploaded or pasted.
- Desired response type: short answer, deep review, experiment plan, implementation checklist, paper brainstorm, etc.
- Local `bridge_thread_id` for the task.
- Local `codex_session_id` and Codex notes path that produced the prompt or bundle, if any.
- Whether this should create a new GPT Pro web conversation or reuse an existing GPT Pro session.
- Local `gpt_pro_session_id` for the GPT Pro session, if reusing or naming one.

## Session storage

Use one flat bridge thread per task and one shallow directory per GPT Pro web conversation:

```text
.codex/codex-pro-bridge/
  threads/
    index.md
    <bridge-thread-id>.md
  bundles/
  codex-sessions/
    index.md
    <codex-session-id>/
      session.md
      notes.md
  gpt-pro-sessions/
    index.md
    <gpt-pro-session-id>/
      session.md
      001-<slug>.md
      002-<slug>.md
```

`threads/<bridge-thread-id>.md` is the task timeline. It includes a Mermaid `gitGraph` view and a full local ledger linking Codex updates, bundles, and GPT Pro turns without copying every artifact into each bundle. `codex-sessions/` stores Codex-side notes used for bundles. `gpt-pro-sessions/index.md` is the lightweight table of known GPT Pro conversations. Each GPT Pro `session.md` records:

- `gpt_pro_session_id`
- `bridge_thread_id`
- `codex_session_id`, when known
- GPT Pro conversation URL
- observed web title
- purpose
- created/last-used timestamps

Each turn file stores the prompt, optional bundle path, full GPT Pro answer, Codex summary, Codex verification notes, and a short decision trail. If no GPT Pro session is specified, create a clear task-scoped `gpt_pro_session_id`. If the user names an existing GPT Pro session, open the URL from that session and append the next numbered turn file.

Use filesystem-safe IDs such as `repo-20260703-short-task`, `repo-20260703-short-task-codex`, and `repo-20260703-short-task-gpt-pro`. Do not use slashes in IDs; one session should stay one shallow directory.

Minimal `session.md`:

```yaml
gpt_pro_session_id: <gpt-pro-session-id>
bridge_thread_id: <bridge-thread-id>
codex_session_id: <codex-session-id>
web_conversation_url: <https://chatgpt.com/c/...>
web_title: <observed title>
purpose: <why this session exists>
created_at: <iso timestamp>
last_used_at: <iso timestamp>
latest_turn: 001
```

Minimal turn file:

```markdown
# 001 <short title>

## Metadata
- Bridge Thread ID:
- Codex Session ID:
- GPT Pro URL:
- Codex Notes:
- Bundle:
- Asked at:

## Prompt
...

## GPT Pro Answer
...

## Codex Summary
...

## Codex Verification
...

## Decision Trail
...
```

Prefer the bundled helper when saving a response, because it updates `session.md`, appends the next turn file, regenerates `gpt-pro-sessions/index.md`, and appends a `gpt-pro-turn` event to the bridge thread:

```bash
python3 .agents/skills/gpt-pro-question-window/scripts/save_bridge_turn.py \
  --repo . \
  --bridge-thread-id repo-20260703-short-task \
  --codex-session-id repo-20260703-short-task-codex \
  --codex-notes .codex/codex-pro-bridge/codex-sessions/repo-20260703-short-task-codex/notes.md \
  --gpt-pro-session-id repo-20260703-short-task-gpt-pro \
  --web-url https://chatgpt.com/c/... \
  --web-title "Observed GPT Pro title" \
  --purpose "Why this GPT Pro session exists" \
  --bundle .codex/codex-pro-bridge/bundles/algorithm-context.md \
  --prompt-file /tmp/gpt-pro-prompt.md \
  --answer-file /tmp/gpt-pro-answer.md \
  --summary-file /tmp/codex-summary.md \
  --verification-file /tmp/codex-verification.md \
  --decision-trail-file /tmp/decision-trail.md
```

## Standard workflow

1. Restate the question and required output format.
2. Check for context files. Exclude `.env`, credentials, cookies, private keys, tokens, databases, raw user data, and other files that clearly should not be uploaded. If unsure, ask the user before proceeding.
3. Open ChatGPT in signed-in Chrome, or use Computer Use if necessary.
4. Start a new task-scoped conversation unless the user explicitly names an existing GPT Pro session or GPT Pro conversation URL.
5. Select the strongest available GPT Pro / reasoning model visible in the UI. If the exact model is unclear, do not claim certainty.
6. Paste the prompt. Attach the context bundle if provided. For code or implementation context, prefer a zip whose `README_FOR_GPT_PRO.md` explains the task while source files remain separate. Do not paste a full code bundle into the chat unless the user explicitly approves that fallback. Use browser pacing: small varied waits, no rapid retries, at most three concurrent conversations, and staggered submissions.
7. Wait for the answer to finish streaming before copying or saving it.
8. Copy the full answer into the current session's next turn file, preferably with `scripts/save_bridge_turn.py` and the same `bridge-thread-id` used for the bundle.
9. Return to the Codex thread and summarize:
   - Which GPT Pro session and turn file were saved.
   - The key useful points.
   - What Codex should verify before acting.

## Prompt wrapper for normal questions

When no specialized skill supplies a prompt, use this wrapper:

```text
You are GPT Pro helping Codex with a task. Codex will use your answer as an external review, not as ground truth.

Task:
<QUESTION>

Context:
<OPTIONAL_CONTEXT_OR_ATTACHMENT_DESCRIPTION>

Please answer with:
1. Direct answer
2. Reasoning / key assumptions
3. Risks or caveats
4. Concrete next actions for Codex

If information is missing, state the uncertainty instead of guessing.
```

## Upload fallback

Try the Chrome file chooser flow first. If DOM-level browser automation does not open the native file chooser, use Computer Use to click ChatGPT's visible upload menu and select the file in the operating system picker. Record the generic upload fallback in the local decision trail when it matters for reproducibility.

If ChatGPT accepts the file but remains in document-reading or file-extraction states for several minutes, treat that as a bundle-shape problem before treating it as an upload failure. Regenerate a smaller zip or at most two or three focused attachments. Keep the prompt short and do not work around the issue by pasting the full bundle text.

## Guardrails

- Never upload `.env`, API keys, cookies, SSH keys, private tokens, database dumps, user data dumps, or credentials.
- Do not automate bulk extraction from ChatGPT.
- Do not use browser automation to bypass rate limits, CAPTCHAs, abuse checks, login checks, or account-security prompts.
- Do not hide uncertainty about what the UI/model actually selected.
- Always save GPT Pro output before acting on it.
- Always re-check GPT Pro suggestions against local files before editing code.
