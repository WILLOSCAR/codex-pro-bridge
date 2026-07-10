---
name: bundle-algorithm-context
description: Build a compact, immutable GPT Pro evidence bundle from repository code, configs, docs, logs, Codex notes, and recent bridge events. Use before algorithm review, paper brainstorm, experiment analysis, implementation consistency review, or any GPT Pro round that needs local evidence.
---

# Bundle Algorithm Context

Build the smallest evidence package that can support the decision. Do not dump the repository.

Before writing bridge state, read the canonical protocol at [../gpt-pro-question-window/references/bridge_protocol.md](../gpt-pro-question-window/references/bridge_protocol.md). Before validating a generated package, read [references/bundle_schema.md](references/bundle_schema.md).

## Required flow

1. Choose one `bridge-thread-id` and a review mode: `algorithm_review`, `experiment_analysis`, `paper_brainstorm`, `implementation_check`, or `general_question`.
2. Inspect repository status, changed and untracked files, relevant architecture/docs, and the user's explicit focus paths.
3. Create an immutable Codex notes snapshot with `scripts/prepare_codex_session_notes.py`.
4. Choose repository context:
   - `auto` for the first evidence-heavy round.
   - `explicit` for follow-ups; name only changed or newly relevant files.
   - `none` for reasoning-only follow-ups.
5. Run `scripts/build_algorithm_bundle.py`, normally with `--format zip`.
6. Open the manifest and verify every supplied and omitted input, repository label, mode-specific output contract, context budget, and safety warning.
7. Upload only after the manifest matches the intended evidence scope.

Completion criterion: the artifact is new and immutable, its zip integrity check passes, no absolute local repository path appears in the manifest, every requested include is accounted for, and GPT Pro can distinguish supplied evidence from missing evidence.

## Selection rules

Prioritize the problem statement, current notes, changed implementation, configs, data/label construction, training or inference logic, evaluation, metrics, result summaries, and exact files named by the user.

Exclude env files, credentials, cookies, keys, databases, raw private data, vendor trees, generated artifacts, large binaries, and unrelated files. Do not rewrite ordinary source contents. Fail on high-confidence secret-like content unless the exact files have been manually reviewed and explicitly allowed.

Reject missing explicit includes and paths outside the repository by default. Use `--allow-external-include` only after confirming every external file; archive names remain anonymized.

## Multi-round policy

Do not resend code on every follow-up. Send current Codex notes and compact thread events, adding repository files only when they changed or GPT Pro needs to inspect them again.

The local JSONL ledger remains complete. A bundle includes the latest 24 events and at most 20,000 thread-context characters by default. Bundle construction is not a timeline event; the artifact is recorded only when `save_bridge_turn.py` captures the exchange that actually used it.
