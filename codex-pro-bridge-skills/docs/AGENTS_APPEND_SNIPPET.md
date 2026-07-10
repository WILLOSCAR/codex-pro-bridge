# AGENTS.md Snippet

```markdown
## Codex Pro Bridge

Use one `bridge-thread-id` per task. Follow `codex-snapshot -> gpt-exchange -> codex-verdict`; keep the raw GPT Pro answer separate from later Codex verification.

Build focused immutable bundles. Default to repository-contained evidence, reject missing or out-of-scope includes, exclude secrets/env/databases/raw private data, and record the sent bundle by relative path plus SHA-256.

In auto mode, keep definitely-local dependency closures whole before adding breadth. In explicit mode, fail if any requested safe file is omitted. Verify the generated manifest before upload.

Before the first GPT Pro browser round, verify that the Codex Chrome extension is installed and enabled. In this environment, installation from the Chrome Web Store requires a US-region network node. In the extension's **Details**, enable **Allow access to file URLs** or local bundles may not attach.

Use signed-in Chrome and its file chooser for GPT Pro. Computer Use is only the fallback for UI boundaries Chrome cannot control. Stop for login, password, 2FA, CAPTCHA, rate-limit, abuse, or account-security prompts.

Use ChatGPT's visible attachment control, verify the visible filename and exact requested model with `check_browser_preflight.py`, submit once, and do not retry while generation remains active. Capture model/upload/timing provenance, then run `verify_bridge_thread.py --require-complete-rounds` before continuing or handing off.

Read `.agents/skills/gpt-pro-question-window/references/bridge_protocol.md` before creating or resuming bridge state.
```
