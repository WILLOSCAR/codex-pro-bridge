# Workflow

Codex Pro Bridge is a supervised `Codex -> ChatGPT/GPT Pro -> Codex` loop. Codex owns repository access, implementation, tests, and final verification.

## Prerequisite

Install and enable the Codex Chrome extension. In the extension's Chrome details page, enable **Allow access to file URLs** so the signed-in browser session can attach local bundles.

## One external round

```text
codex-snapshot -> gpt-exchange -> codex-verdict
```

1. Write current notes and an immutable `codex-snapshot`.
2. Build a focused bundle and inspect its manifest.
3. Upload through ChatGPT's visible attachment control.
4. Verify the exact visible model label and attachment name with `check_browser_preflight.py`.
5. Submit once and wait through visible completion without resending.
6. Capture the full raw answer and browser provenance as `gpt-exchange`.
7. Re-open local evidence, verify actionable claims, and record a separate `codex-verdict`.
8. Run `verify_bridge_thread.py --require-complete-rounds`.

Repeat only when another round has a concrete unresolved question. Reuse the same bridge thread and compatible web conversation.

## Evidence modes

- `auto`: required focus, conservative relative JS/TS and Python dependency closure, then ranked breadth.
- `explicit`: only named files; missing, filtered, or over-budget requested evidence fails by default.
- `none`: no repository source; current notes and compact thread context only.

Bundle drafts do not enter the timeline. The sent bundle is recorded only when its exchange is captured.

## Browser rules

- Use a visible attachment button and visible upload menu item; never directly click hidden `#upload-files`.
- `Pro`, `极高`, and an account name containing “Pro” are different observations.
- Exact visible selection does not attest backend model identity.
- While generation remains active, keep waiting and report progress instead of submitting again.
- Stop for login, password, 2FA, CAPTCHA, rate limits, abuse warnings, or account-security prompts.

## State

JSONL under `.codex/codex-pro-bridge/threads/` is canonical. Markdown timelines, indexes, and sequence diagrams are derived views.

Sessions cannot move between bridge threads. GPT Pro sessions cannot move between ChatGPT conversation URLs. Immutable artifacts cannot be overwritten.

For commands, storage, budgets, legacy migration, and full invariants, read [the canonical bridge protocol](../.agents/skills/gpt-pro-question-window/references/bridge_protocol.md).
