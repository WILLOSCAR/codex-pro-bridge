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
   - `auto` for the first evidence-heavy round. Pass important `--include` paths as required focus seeds; the builder closes definitely-local dependencies before adding breadth.
   - `explicit` for follow-ups; name every required file. Safe requested files such as `.mjs` must appear in the manifest or the build fails.
   - `none` for reasoning-only follow-ups; `--max-files 0` is valid.
5. Run `scripts/build_algorithm_bundle.py`, normally with `--format zip`.
6. Open the manifest and verify every supplied and omitted input, selection reason, dependency closure, repository label, mode-specific output contract, context budget, and safety warning.
7. Upload only after the manifest matches the intended evidence scope.

Completion criterion: the artifact is new and immutable, its zip integrity check passes, no absolute local repository path appears in the manifest, every requested include is present or the build failed, the auto dependency closure is complete, and GPT Pro can distinguish supplied evidence from missing evidence.

## Selection rules

Prioritize the problem statement, current notes, changed implementation, configs, data/label construction, training or inference logic, evaluation, metrics, result summaries, and exact files named by the user.

Exclude env files, credentials, cookies, keys, databases, raw private data, vendor trees, generated artifacts, large binaries, and unrelated files. Do not rewrite ordinary source contents. Fail on high-confidence secret-like content unless the exact files have been manually reviewed and explicitly allowed.

Reject missing, filtered, or over-budget explicit includes and paths outside the repository by default. Use `--allow-incomplete-includes` only after inspecting and accepting every recorded evidence gap. Use `--allow-external-include` only after confirming every external file; archive names remain anonymized.

Do not use file count as a completeness signal. Selection priority is: explicit focus, question-named definitions, definitely-local dependencies, matching tests and boundary partners, runtime/compiled counterparts, relevant changed files, then general repository context. If a required closure exceeds the budget, increase the reviewed budget or narrow the focus; do not upload a silently truncated closure. Use `--allow-incomplete-auto-context` only after inspecting every unresolved local dependency and accepting the evidence gap.

## Multi-round policy

Do not resend code on every follow-up. Send current Codex notes and compact thread events, adding repository files only when they changed or GPT Pro needs to inspect them again.

The local JSONL ledger remains complete. A bundle includes the latest 24 events and at most 20,000 thread-context characters by default. Bundle construction is not a timeline event; the artifact is recorded only when `save_bridge_turn.py` captures the exchange that actually used it.
