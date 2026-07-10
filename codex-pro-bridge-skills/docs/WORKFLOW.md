# Workflow

Codex Pro Bridge connects two complementary roles:

- Codex understands and operates the local repository.
- GPT Pro provides an independent reasoning and review pass over scoped evidence.

The bridge keeps the handoff reproducible without treating the external answer as repository truth.

## One review loop

1. Codex identifies the concrete decision that needs outside reasoning.
2. Codex prepares the smallest evidence package that can support that decision.
3. GPT Pro reviews the supplied evidence and returns an external analysis.
4. Codex verifies actionable claims against local code, tests, configs, data, and logs.
5. Codex implements supported changes, plans an experiment, or asks one focused follow-up.

## Context across rounds

The first implementation-heavy round usually includes source, configuration, documentation, and relevant results.

Follow-up rounds reuse current Codex notes and compact task context. Source is added again only when it changed or must be inspected again.

Three evidence modes support this:

- `auto`: select a relevant focus and conservative local dependencies.
- `explicit`: send only named files for a focused follow-up.
- `none`: continue reasoning without resending repository source.

## Responsibility split

GPT Pro can challenge assumptions, propose alternatives, identify missing experiments, and pressure-test a research or implementation story.

Codex remains responsible for repository facts, edits, test execution, result interpretation, and the final decision.

For operational commands and invariants, read the relevant skill and [the canonical bridge protocol](../.agents/skills/gpt-pro-question-window/references/bridge_protocol.md).
