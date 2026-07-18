---
name: gpt-pro-algorithm-pipeline
description: Run the end-to-end Codex to GPT Pro to Codex loop for algorithm, research, experiment, or implementation-consistency work. Use when the user wants scoped evidence bundling, deep external review, local verification, experiments, and safe implementation connected through one reproducible task thread.
---

# GPT Pro Algorithm Pipeline

Orchestrate the existing bridge skills; do not duplicate their implementation.

Read the canonical state and lifecycle rules at [../gpt-pro-question-window/references/bridge_protocol.md](../gpt-pro-question-window/references/bridge_protocol.md) before the first external round.

## Pipeline

1. Determine the decision the user needs and select `algorithm_review`, `experiment_analysis`, `paper_brainstorm`, `implementation_check`, or `general_question`.
2. Use `$gpt-pro-project-workspace` to preview routing. Stop and work locally
   for `local_only`; repair or verify an ambiguous Project binding; otherwise
   preserve the returned Project, Thread, and conversation policy for the
   entire round.
3. Use `$bundle-algorithm-context` to write current Codex notes and build a focused evidence artifact on that identity.
4. Select the prompt family:
   - `$gpt-pro-research-algorithm-reviewer` for algorithm, pipeline, and experiment critique.
   - `$gpt-pro-paper-brainstormer` for paper framing.
   - `$gpt-pro-question-window` for a normal question.
5. Use `$gpt-pro-question-window` to upload through a visible control, verify the attachment, gate the exact requested model with `check_browser_preflight.py`, send once, wait through visible completion, and capture the full raw exchange plus browser provenance on the same `bridge-thread-id`.
6. Re-open the repository evidence and classify every actionable claim as verified, partially verified, unsupported, or inapplicable.
7. Record that result as a separate `codex-verdict` event before implementing it.
8. Use `$experiment-plan-generator` when empirical evidence is required. Prefer the cheapest sanity or kill experiment before expensive work.
9. Implement only changes authorized by the user and justified by the local verdict. Run focused checks while editing and the relevant full suite at the end.
10. Use `$implementation-consistency-checker` before trusting experimental results or declaring the implementation complete.
11. Verify the thread with `verify_bridge_thread.py --require-complete-rounds`;
    in Project mode also verify the Bridge Project. Update Codex notes and
    repeat only when another GPT Pro round has a concrete unresolved question.

Completion criterion: the thread contains a verified snapshot/exchange/verdict chain for every external round; exact model and attachment provenance are recorded; the final answer distinguishes GPT Pro advice from locally verified facts; implemented changes and tests are recorded; and no open critical inconsistency is hidden.

## Handoff to the user

Report:

- Evidence artifact and GPT Pro turn.
- Main external conclusions.
- Codex verification, including rejected or unsupported claims.
- Experiment or implementation performed.
- Tests and remaining uncertainty.
- Go, iterate, or stop decision.

Do not use this full pipeline for a small local bug, formatting change, or task that does not benefit from external reasoning.

`standalone` remains a complete path for a small project or one-off review.
Project mode adds shared context and task organization; it does not replace the
existing thread protocol.
