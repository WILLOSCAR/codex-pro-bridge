# Usage Prompts

## Normal question

```text
Use $gpt-pro-question-window.
Use bridge thread [bridge-thread-id] and ask GPT Pro:
[question]
Capture the raw exchange, verify it locally, and record a separate Codex verdict.
Require the exact visible Pro selection and verify the completed bridge thread.
```

## Existing conversation

```text
Use $gpt-pro-question-window.
Reuse GPT Pro session [gpt-pro-session-id] only if it is already bound to bridge thread [bridge-thread-id] and the intended web conversation.
Ask:
[follow-up]
Use notes and compact events by default; add files only when they changed or need another read.
Preserve the prior model provenance and verify the thread before this follow-up.
```

## Deep algorithm review

```text
Use $gpt-pro-research-algorithm-reviewer.
Use bridge thread [bridge-thread-id].
Goal: [goal]
Focus files: [optional repository paths]
Decision needed: [go/no-go, experiment, implementation, or diagnosis]
After GPT Pro answers, verify every actionable claim against the repository and record the verdict.
Require exact visible Pro selection; do not treat the UI label as backend attestation.
```

## Paper brainstorm

```text
Use $gpt-pro-paper-brainstormer.
Use bridge thread [bridge-thread-id] to evaluate:
[idea]
Mark related-work names as unverified unless supported by supplied evidence or current research.
Capture the raw answer and record a separate local verdict.
```

## Full pipeline

```text
Use $gpt-pro-algorithm-pipeline.
Run the full Codex -> GPT Pro -> Codex loop for:
[task]
Keep one bridge thread, send scoped evidence, preserve raw answers, record local verdicts, and implement only verified changes.
Use exact browser preflight and verify every completed round before handoff.
```
