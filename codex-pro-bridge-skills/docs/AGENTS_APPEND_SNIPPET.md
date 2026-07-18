# AGENTS.md Snippet

```markdown
## Codex Pro Bridge

Resolve every request to `local_only`, `standalone`, or `project` before writing bridge state. Preserve standalone for one-off work; when a repository has a Bridge Project, use or repair its one-to-one ChatGPT Project binding instead of silently bypassing it.

Use one `bridge-thread-id` per task. The Bridge Thread is also the Project task identity; do not add a parallel Workstream ID. Follow `codex-snapshot -> gpt-exchange -> codex-verdict`; keep the raw GPT Pro answer separate from later Codex verification.

Build focused immutable bundles. Default to repository-contained evidence, reject missing or out-of-scope includes, exclude secrets/env/databases/raw private data, and record the sent bundle by relative path plus SHA-256.

In auto mode, keep definitely-local dependency closures whole before adding breadth. In explicit mode, fail if any requested safe file is omitted. Verify the generated manifest before upload.

Treat stable Project Sources and round-scoped Task Bundles as different artifacts. Never automatically delete user-managed Project files. Verify the visible Project ID, account/workspace, model, and attachment before submission.

Read `.agents/skills/gpt-pro-question-window/references/bridge_protocol.md` and `.agents/skills/gpt-pro-project-workspace/references/project_protocol.md` before creating or resuming bridge state.
```
