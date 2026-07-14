# Codex Pro Bridge

[中文说明](README.zh-CN.md)

## Introduction

Codex Pro Bridge is built for work that moves back and forth between algorithm design, research reasoning, and engineering implementation.

Codex and GPT Pro are both useful, but they are useful in different places and at different speeds.

Codex belongs close to the local repository. It can read files, change code, run tests, inspect logs, and check whether a proposal actually matches the current implementation.

GPT Pro is better suited to slower external reasoning: challenging an algorithm, mapping failure modes, designing ablations, planning experiments, framing a paper, or trying to disprove an idea.

The fragile part is the handoff between them.

### The problem

Without a bridge, the workflow becomes a loop through the repository, Codex, and a browser, held together by manual copy and paste.

The external model does not automatically know the repository state, current implementation, local experiments, or decisions already made by Codex.

Providing that context quickly becomes a choice between too little and too much.

Too little context produces confident advice about code the reviewer never saw. Too much context is noisy, expensive to inspect, hard to update, and more likely to include unrelated or sensitive material.

### The workflow pain

The handoff creates several kinds of loss:

- **Attention loss:** work repeatedly switches between the repository, Codex, and the browser.
- **Structure loss:** prompts, files, assumptions, follow-ups, and decisions scatter as they are copied between tools.
- **Reproducibility loss:** later it becomes difficult to know exactly what evidence GPT Pro reviewed.
- **State drift:** the web conversation continues while the repository, experiments, and implementation change underneath it.
- **Implementation loss:** a useful algorithm or research idea never makes it back into tests, configs, logs, and working code.

In one round, these are workflow frictions. Across several rounds, they make the review difficult to reuse, audit, or implement.

The missing piece is not another chat window. It is a reproducible handoff between local execution and external reasoning.

### The idea

Codex Pro Bridge is not an API client, and it does not let GPT Pro edit local files directly.

It is a local workflow layer made of Codex skills and helper scripts. It turns a GPT Pro web review into engineering material that can be reused, traced, and connected back to the real repository.

The workflow is:

1. Codex writes local notes for one concrete task.
2. Codex builds a bundle with an explicit evidence boundary.
3. GPT Pro reviews that scoped material in the web conversation.
4. Codex saves the complete answer, summarizes it, and verifies actionable claims locally.
5. The same task continues into implementation, experiments, or another focused review round.

```mermaid
sequenceDiagram
  participant C as Codex
  participant G as GPT Pro
  C->>C: Inspect repository and write task notes
  C->>G: Evidence-bounded bundle + focused question
  G-->>C: External review
  C->>C: Save, summarize, and verify the full answer
  C->>C: Implement, experiment, or continue the same task
```

The goal is to make the loop reusable, auditable, and implementable. Codex remains the source of truth; GPT Pro remains an external reviewer.

## How it works

Each task uses one bridge thread so the evidence, external review, local verdict, implementation, and later follow-ups remain connected.

The bridge keeps three concerns separate:

- **Evidence construction:** build the smallest package that can support the decision.
- **External reasoning:** ask a focused question against exactly that evidence.
- **Local verification:** check every actionable conclusion before changing code or trusting a result.

### Evidence modes

| Mode | Use it for | Repository source |
| --- | --- | --- |
| `auto` | First implementation-heavy round | Select a focus, close conservative local dependencies, then add relevant breadth |
| `explicit` | Focused follow-up | Send only the files that need another review |
| `none` | Reasoning-only follow-up | Reuse current notes and task context without resending source |

Auto mode follows definitely-local relative imports for JavaScript/TypeScript and Python. Modern Node source and test files such as `.mjs`, `.cjs`, `.mts`, and `.cts` are supported.

Follow-up rounds normally reuse current Codex notes and compact task history. Source is added again only when it changed or the reviewer needs to inspect it.

## When to use it

Codex Pro Bridge is useful when the decision benefits from an independent reasoning pass:

- Reviewing an algorithm, training pipeline, reward design, or evaluation method.
- Stress-testing a research claim, paper framing, novelty argument, or reviewer story.
- Turning a proposal into baselines, ablations, metrics, and decision rules.
- Checking whether code, configs, data splits, commands, logs, and reported results agree.
- Carrying a complex review across several rounds without losing provenance.

For a small local bug, formatting change, or straightforward implementation task, Codex should usually work directly in the repository without this bridge.

## Quick start

### Install

Global installation:

```bash
./codex-pro-bridge-skills/install.sh --global
```

Repository-local installation:

```bash
./codex-pro-bridge-skills/install.sh --repo /path/to/repo
```

The bridge uses the signed-in Chrome session available to Codex. The selected skill checks browser prerequisites when an external round begins.

Restart Codex or open a new task if an existing task does not discover the updated skills.

### Ask a normal question

```text
Use $gpt-pro-question-window.
Use bridge thread <repo>-<date>-<task> and ask GPT Pro:
<question>
Capture the raw answer, verify it locally,
and record a separate Codex verdict.
```

### Run the full algorithm or research loop

```text
Use $gpt-pro-algorithm-pipeline.
Run the Codex -> GPT Pro -> Codex loop for:
<task>
Keep one bridge thread, send only scoped evidence,
and implement only locally verified changes.
```

More examples are available in [examples/usage_prompts.md](codex-pro-bridge-skills/examples/usage_prompts.md).

## Skills

| Skill | Purpose |
| --- | --- |
| `gpt-pro-question-window` | Ask a normal question or continue an existing external review |
| `bundle-algorithm-context` | Build a scoped evidence package for a source-backed round |
| `gpt-pro-research-algorithm-reviewer` | Review algorithms, pipelines, experiments, and research claims |
| `gpt-pro-paper-brainstormer` | Develop paper framing, novelty, objections, and experiment story |
| `experiment-plan-generator` | Convert an idea or review into an experiment matrix |
| `implementation-consistency-checker` | Check consistency across proposal, code, configs, data, evaluation, and results |
| `gpt-pro-algorithm-pipeline` | Run the complete evidence, review, verification, experiment, and implementation loop |

Use `$experiment-plan-generator` and `$implementation-consistency-checker` locally when outside reasoning is unnecessary.

## Development

```bash
cd codex-pro-bridge-skills
python3 -m unittest discover -s tests -v
python3 tests/validate_skills.py
```

## Documentation

- [Workflow overview](codex-pro-bridge-skills/docs/WORKFLOW.md)
- [Canonical bridge protocol](codex-pro-bridge-skills/.agents/skills/gpt-pro-question-window/references/bridge_protocol.md)
- [Evidence bundle schema](codex-pro-bridge-skills/.agents/skills/bundle-algorithm-context/references/bundle_schema.md)
- [AGENTS.md integration snippet](codex-pro-bridge-skills/docs/AGENTS_APPEND_SNIPPET.md)

## Star History

[![GitHub Stars](https://img.shields.io/github/stars/WILLOSCAR/codex-pro-bridge?style=flat-square&label=GitHub%20Stars)](https://www.star-history.com/#WILLOSCAR/codex-pro-bridge&Date)

## Acknowledgements

Thanks to the [Linux.do](https://linux.do/) community for its discussions, feedback, and support.
