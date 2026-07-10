# Workflow

Before the first browser round, install and enable the Codex Chrome extension. In this environment, use a US-region network node while downloading it from the Chrome Web Store. Then open `chrome://extensions/` -> Codex -> **Details** and enable **Allow access to file URLs**; otherwise the local bundle may not be attached.

The bridge has one task identity and three event types:

```text
codex-snapshot -> gpt-exchange -> codex-verdict
```

1. Write current notes and an immutable snapshot.
2. Build a focused bundle. Auto mode closes definitely-local dependencies before breadth; explicit mode fails when a requested safe file is omitted. Local build attempts do not enter the task timeline.
3. Upload through a visible signed-in Chrome control, verify the attachment and exact requested model with `check_browser_preflight.py`, submit once, and capture the full raw exchange with browser provenance and the bundle digest.
4. Verify locally and record a separate immutable verdict.
5. Run `verify_bridge_thread.py --require-complete-rounds` and repeat only for a concrete unresolved question, reusing the same bridge thread.

Use `auto` repository context on the first implementation-heavy round, `explicit` for focused follow-ups, and `none` for reasoning-only follow-ups.

The canonical ledger is JSONL. Markdown, indexes, and the two-lane sequence view are derived. Sessions cannot move between threads, GPT Pro sessions cannot change ChatGPT URLs, artifacts cannot be overwritten, and the verifier checks parents plus artifact and bundle hashes.

Do not directly click hidden `#upload-files`. Use the visible attachment/menu route, then wait while generation remains visibly active without resubmitting. Record mismatch or unverified model states truthfully even when the raw answer is still captured.

For IDs, storage, CLI commands, context budgets, browser behavior, safety rules, and legacy migration, read [the canonical bridge protocol](../.agents/skills/gpt-pro-question-window/references/bridge_protocol.md).
