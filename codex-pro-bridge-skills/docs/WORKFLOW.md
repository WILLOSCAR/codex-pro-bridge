# Workflow

Codex Pro Bridge connects two complementary roles:

- Codex understands and operates the local repository.
- GPT Pro provides an independent reasoning and review pass over scoped evidence.

The bridge keeps the handoff reproducible without treating the external answer as repository truth.

## One review loop

1. Codex identifies the concrete decision that needs outside reasoning.
2. The router chooses `local_only`, `standalone`, or `project`.
3. Codex prepares the smallest evidence package that can support that decision.
4. GPT Pro reviews the supplied evidence in the task's exact conversation.
5. Codex verifies actionable claims against local code, tests, configs, data, and logs.
6. Codex implements supported changes, plans an experiment, or asks one focused follow-up.

## Two external-review shapes

Standalone mode is the default lightweight shape:

```text
Bridge Thread
  Codex Session
  GPT Conversation
```

Project mode adds durable shared context for a long-running repository:

```text
Bridge Project
  Local repository binding
  0 or 1 ChatGPT Project binding
  Project Sources
  N Bridge Threads
    Codex Session
    GPT Conversation
```

One repository maps to at most one Bridge Project, which maps to at most one
current ChatGPT Project. The Bridge Thread is already the task identity.
Standalone threads can be attached later without rewriting their event history.

## Context across rounds

The first implementation-heavy round usually includes source, configuration, documentation, and relevant results.

Follow-up rounds reuse current Codex notes and compact task context. Source is added again only when it changed or must be inspected again.

Three evidence modes support this:

- `auto`: select a relevant focus and conservative local dependencies.
- `explicit`: send only named files for a focused follow-up.
- `none`: continue reasoning without resending repository source.

Stable cross-conversation material belongs in Project Sources. Round-specific
evidence belongs in Task Bundles. The default `append_only` mode retains old
versions. Explicit `managed` replacement uploads and verifies the new
digest-named version before removing an older bridge-managed version;
user-managed remote files are never automatically removed.

## Responsibility split

GPT Pro can challenge assumptions, propose alternatives, identify missing experiments, and pressure-test a research or implementation story.

Codex remains responsible for repository facts, edits, test execution, result interpretation, and the final decision.

For operational commands and invariants, read the relevant skill,
[the canonical bridge protocol](../.agents/skills/gpt-pro-question-window/references/bridge_protocol.md),
and [the Project protocol](../.agents/skills/gpt-pro-project-workspace/references/project_protocol.md).
