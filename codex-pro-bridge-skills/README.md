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

The installer replaces only the seven managed skills and `.shared`. Repository-local installation also adds `.agents/` and `.codex/` to the target repository's local `.git/info/exclude`.

## Entry points

| Need | Skill |
| --- | --- |
| Normal question or existing conversation | `$gpt-pro-question-window` |
| Deep algorithm, pipeline, or experiment review | `$gpt-pro-research-algorithm-reviewer` |
| Paper framing and reviewer pressure test | `$gpt-pro-paper-brainstormer` |
| Complete external-review loop | `$gpt-pro-algorithm-pipeline` |
| Local experiment matrix | `$experiment-plan-generator` |
| Local implementation/result consistency check | `$implementation-consistency-checker` |

`$bundle-algorithm-context` prepares evidence for source-backed external rounds.

See [usage_prompts.md](examples/usage_prompts.md) for invocation examples. Operational details live in the relevant skills and the [canonical protocol](.agents/skills/gpt-pro-question-window/references/bridge_protocol.md).

## Validate

```bash
python3 -m unittest discover -s tests -v
python3 tests/validate_skills.py
```
