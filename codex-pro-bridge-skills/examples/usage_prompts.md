# Usage Prompts

## Normal question

```text
Use $gpt-pro-question-window.
Route this task automatically and ask GPT Pro:
[question]
Capture the raw exchange, verify it locally, and record a separate Codex verdict.
```

## Existing ChatGPT Project

```text
Use $gpt-pro-project-workspace.
Bind this repository to my existing ChatGPT Project.
Verify the exact visible Project ID, account, and workspace.
Plan stable shared sources, but do not remove user-managed files.
```

## Project-aware question

```text
Use $gpt-pro-question-window.
Ask GPT Pro inside this repository's bound Project:
[question]
Reuse an exact matching task when one exists; otherwise create a new Bridge
Thread and Project conversation. Preserve the route and record both identities.
```

## Existing conversation

```text
Use $gpt-pro-question-window.
Reuse GPT Pro session [gpt-pro-session-id] only if it is already bound to bridge thread [bridge-thread-id] and the intended web conversation.
Ask:
[follow-up]
Use notes and compact events by default; add files only when they changed or need another read.
```

## Deep algorithm review

```text
Use $gpt-pro-research-algorithm-reviewer.
Route the task automatically.
Goal: [goal]
Focus files: [optional repository paths]
Decision needed: [go/no-go, experiment, implementation, or diagnosis]
After GPT Pro answers, verify every actionable claim against the repository and record the verdict.
```

## Paper brainstorm

```text
Use $gpt-pro-paper-brainstormer.
Route the task automatically and evaluate:
[idea]
Mark related-work names as unverified unless supported by supplied evidence or current research.
```

## Full pipeline

```text
Use $gpt-pro-algorithm-pipeline.
Run the full Codex -> GPT Pro -> Codex loop for:
[task]
Resolve local, standalone, or Project scope first. Keep one thread for the
deliverable, send scoped evidence, preserve raw answers, record local verdicts,
and implement only verified changes.
```
