# Optional AGENTS.md Snippet

Add this to your repo's AGENTS.md if you want Codex to remember the workflow.

```markdown
## Codex Pro Bridge Algorithm Review Workflow

For high-value algorithm, RL, reward, OPD, agentic, search/QA, evaluation, or paper-idea tasks, do not rely only on local coding intuition. Prefer the repo skills:

- `$gpt-pro-algorithm-pipeline` for end-to-end review.
- `$bundle-algorithm-context` to prepare minimal context.
- `$gpt-pro-research-algorithm-reviewer` for deep algorithm review.
- `$gpt-pro-paper-brainstormer` for paper framing.
- `$experiment-plan-generator` for experiment matrices.
- `$implementation-consistency-checker` before trusting results.

Role split:

- Codex reads local repo, builds bundles, implements changes, runs tests, and verifies consistency.
- GPT Pro reviews algorithm hypotheses, experiment design, novelty, and failure modes.

Use one `bridge-thread-id` for each task and reuse it across Codex -> GPT Pro -> Codex rounds. Before building a GPT Pro bundle, write Codex-side session notes under `.codex/codex-pro-bridge/codex-sessions/<codex-session-id>/notes.md`; this updates `.codex/codex-pro-bridge/threads/<bridge-thread-id>.md`. Include a detailed summary of the current Codex reasoning path and, when available, roughly the last 10 relevant raw user/assistant turns. If raw history is unavailable, say so; do not invent missing turns. Keep Codex session IDs and GPT Pro session IDs separate. Keep the full thread ledger locally, but send GPT Pro only compact recent thread context unless older events are directly relevant. Do not resend repository files on every follow-up: use `--repo-context explicit` without `--include` for notes-plus-graph rounds, and add explicit files only when GPT Pro needs to reread code/configs.

Never upload `.env` files, secrets, credentials, cookies, private keys, databases, or raw private data to GPT Pro. Normal included source/config/doc/log contents do not need content rewriting; prefer excluding unsafe files by path/name. If ChatGPT login is required, ask the user to log in manually; do not enter passwords or 2FA codes.

When operating ChatGPT in Chrome, use human-paced browser actions: prefer one conversation, allow two or three when useful, never exceed three concurrent GPT Pro conversations, stagger paste/upload/submit/copy actions, and avoid rapid retries. Stop for CAPTCHA, rate-limit, abuse-warning, unusual login, or account-security prompts and let the user handle them manually.
```
