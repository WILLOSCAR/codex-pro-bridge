# AGENTS.md Snippet

```markdown
## Codex Pro Bridge

Use one `bridge-thread-id` per task. Follow `codex-snapshot -> gpt-exchange -> codex-verdict`; keep the raw GPT Pro answer separate from later Codex verification.

Build focused immutable bundles. Default to repository-contained evidence, reject missing or out-of-scope includes, exclude secrets/env/databases/raw private data, and record the sent bundle by relative path plus SHA-256.

In auto mode, keep definitely-local dependency closures whole before adding breadth. In explicit mode, fail if any requested safe file is omitted. Verify the generated manifest before upload.

Read `.agents/skills/gpt-pro-question-window/references/bridge_protocol.md` before creating or resuming bridge state.
```
