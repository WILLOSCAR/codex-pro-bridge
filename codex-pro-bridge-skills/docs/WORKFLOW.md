# Workflow Details

## Minimal loop

```text
1. User asks Codex for algorithm/pipeline/research review.
2. Pick one `bridge-thread-id` for the whole task.
3. Codex writes Codex-side session notes: detailed summary plus recent raw turns when available.
4. Codex builds a curated context bundle that includes those notes and a compact recent bridge thread window by default.
5. Codex opens signed-in ChatGPT/GPT Pro via Chrome or Computer Use.
6. Codex submits the bundle plus a specialized prompt.
7. GPT Pro returns deep review.
8. Codex saves the review as a turn in the matching GPT Pro session.
9. Codex verifies the review against local code/config/logs.
10. Codex updates the same Codex notes with verification, implementation results, and the next question.
11. If another GPT Pro round is useful, build the next bundle with the same `bridge-thread-id`.
```

## Why there is a separate question-window skill

The core dependency is not algorithm logic. The core dependency is a stable bridge:

```text
Codex controls signed-in browser/computer
  -> opens GPT Pro thread
  -> asks question
  -> attaches/pastes curated context
  -> saves answer
```

Specialized skills simply change the prompt and the bundle.

## Browser pacing

Treat ChatGPT as a user-facing web UI, not a batch API:

- Prefer one GPT Pro conversation, allow two or three when useful, and never exceed three concurrent GPT Pro conversations.
- Add small varied waits between paste, upload, submit, copy, and navigation actions.
- Wait for upload completion and answer streaming completion before continuing.
- Avoid rapid retries, bulk extraction, scraping, and burst-style simultaneous submissions.
- Stop and ask the user to handle CAPTCHA, rate-limit, abuse-warning, unusual login, or account-security prompts.

## Prompt families

- Normal question: `gpt-pro-question-window`
- Deep algorithm review: `gpt-pro-research-algorithm-reviewer`
- Paper/research brainstorm: `gpt-pro-paper-brainstormer`
- Experiment matrix: `experiment-plan-generator`
- Code/config/eval consistency: `implementation-consistency-checker`

## Recommended naming

Use one bridge thread for the whole task, then keep Codex-side session IDs and GPT-Pro-side session IDs separate. A Codex session tracks the local Codex reasoning context; a GPT Pro session maps to one GPT Pro web conversation. Keep all IDs filesystem-safe and shallow:

```text
Bridge thread: <repo>-<YYYYMMDD>-<short-task>
Codex session: <bridge-thread-id>-codex
GPT Pro session: <bridge-thread-id>-gpt-pro
```

Save outputs under:

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

Each call that writes Codex notes, builds a bundle, or saves a GPT Pro turn should use the same `bridge-thread-id`. The thread file is a compact timeline; the Codex notes, bundles, and GPT Pro turn files remain the durable artifacts.

Each thread file should keep a Mermaid `gitGraph` view above the full local timeline. Each bundle should include `codex-sessions/<codex-session-id>/notes.md` and a compact bridge thread context window. Each GPT Pro turn should save the prompt, bundle path or pasted-context description, full GPT Pro answer, Codex summary, Codex verification notes, and a short decision trail. Prefer `gpt-pro-question-window/scripts/save_bridge_turn.py` so `session.md`, the turn file, `gpt-pro-sessions/index.md`, and `threads/index.md` stay aligned.

Codex session notes should be detailed. Include the current goal, hypothesis, decisions made, rejected ideas, open questions, inspected files, desired GPT Pro focus, and roughly the last 10 relevant raw user/assistant turns when available. If raw history cannot be accessed by the runtime, state that explicitly in the notes.

## Context Budget

Do not upload the whole local ledger on long tasks. Keep the full timeline locally, but put only a recent window in GPT Pro bundles:

- Thread file graph: latest 40 events.
- Bundle thread context: latest 24 events by default.
- Bundle thread budget: 20,000 characters by default.
- Override with `--max-thread-events` and `--max-thread-chars` only when the added history is directly relevant.
- Use `--repo-context auto` for initial evidence-heavy rounds.
- Use `--repo-context explicit` for follow-up rounds; add `--include` only for files that changed or need review.
- Use `--repo-context none` for pure reasoning follow-ups where code/config evidence is irrelevant.
