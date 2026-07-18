# Codex Pro Bridge Skills Package

This directory contains the managed skills, shared runtime, installers, tests, examples, and internal workflow references for Codex Pro Bridge.

Start with the repository-level [English README](../README.md) or [中文说明](../README.zh-CN.md).

## Install

Global installation:

```bash
./install.sh --global
```

Repository-local installation:

```bash
./install.sh --repo /path/to/repo
```

The installer replaces only the eight managed skills and `.shared`. Repository-local installation also adds `.agents/` and `.codex/` to the target repository's local `.git/info/exclude`.

## Entry points

| Need | Skill |
| --- | --- |
| Bind, inspect, route, or repair a ChatGPT Project | `$gpt-pro-project-workspace` |
| Normal question or existing conversation | `$gpt-pro-question-window` |
| Deep algorithm, pipeline, or experiment review | `$gpt-pro-research-algorithm-reviewer` |
| Paper framing and reviewer pressure test | `$gpt-pro-paper-brainstormer` |
| Complete external-review loop | `$gpt-pro-algorithm-pipeline` |
| Local experiment matrix | `$experiment-plan-generator` |
| Local implementation/result consistency check | `$implementation-consistency-checker` |

`$gpt-pro-question-window` resolves `local_only`, `standalone`, and `project`
routes automatically. `$bundle-algorithm-context` prepares evidence for
source-backed external rounds.

See [usage_prompts.md](examples/usage_prompts.md) for invocation examples.
Operational details live in the relevant skills, the
[canonical protocol](.agents/skills/gpt-pro-question-window/references/bridge_protocol.md),
and the
[Project protocol](.agents/skills/gpt-pro-project-workspace/references/project_protocol.md).
