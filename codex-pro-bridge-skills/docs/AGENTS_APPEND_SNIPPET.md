# AGENTS.md Snippet

```markdown
## Codex Pro Bridge

Use one `bridge-thread-id` per task. Follow `codex-snapshot -> gpt-exchange -> codex-verdict`; keep the raw GPT Pro answer separate from later Codex verification.

Build focused immutable bundles. Default to repository-contained evidence, reject missing or out-of-scope includes, exclude secrets/env/databases/raw private data, and record the sent bundle by relative path plus SHA-256.

Before the first GPT Pro browser round, verify that the Codex Chrome extension is installed and enabled. In this environment, installation from the Chrome Web Store requires a US-region network node. In the extension's **Details**, enable **Allow access to file URLs** or local bundles may not attach.

Use signed-in Chrome and its file chooser for GPT Pro. Computer Use is only the fallback for UI boundaries Chrome cannot control. Stop for login, password, 2FA, CAPTCHA, rate-limit, abuse, or account-security prompts.

Read `.agents/skills/gpt-pro-question-window/references/bridge_protocol.md` before creating or resuming bridge state.
```
