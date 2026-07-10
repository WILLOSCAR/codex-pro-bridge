# Codex Pro Bridge Skills Package

This directory contains the seven managed skills, shared runtime, installers, tests, examples, and workflow documentation for Codex Pro Bridge.

Start with the repository-level [English README](../README.md) or [中文说明](../README.zh-CN.md). The canonical state and CLI contract lives in [bridge_protocol.md](.agents/skills/gpt-pro-question-window/references/bridge_protocol.md).

## Install

Before browser upload, enable the Codex Chrome extension and turn on **Allow access to file URLs** in the extension's Chrome details page.

Global installation:

```bash
./install.sh --global
```

Repository-local installation:

```bash
./install.sh --repo /path/to/repo
```

The installer replaces only the seven managed skills and `.shared`. It removes generated Python cache files and leaves unrelated skills untouched.

Repository-local installation adds `.agents/` and `.codex/` to the target repository's local `.git/info/exclude`.

## Entry points

| Need | Skill |
| --- | --- |
| Normal question or existing conversation | `$gpt-pro-question-window` |
| Deep algorithm, pipeline, or experiment review | `$gpt-pro-research-algorithm-reviewer` |
| Paper framing and reviewer pressure test | `$gpt-pro-paper-brainstormer` |
| Complete external-review loop | `$gpt-pro-algorithm-pipeline` |
| Local experiment matrix | `$experiment-plan-generator` |
| Local implementation/result consistency check | `$implementation-consistency-checker` |

`$bundle-algorithm-context` is the evidence-package adapter used before source-backed external rounds.

## Runtime contract

Every completed external round is:

```text
codex-snapshot -> gpt-exchange -> codex-verdict
```

Use one `bridge-thread-id`. Preserve raw external answers separately from Codex verdicts. Run exact browser preflight before submission and thread verification before follow-up or handoff.

See [WORKFLOW.md](docs/WORKFLOW.md) for the compact flow and [usage_prompts.md](examples/usage_prompts.md) for invocation examples.

## Validate

```bash
python3 -m unittest discover -s tests -v
python3 tests/validate_skills.py
```

The repository validation path uses only the Python standard library.
