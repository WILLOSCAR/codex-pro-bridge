# Codex Pro Bridge Skills Package

This directory contains the managed skills, shared runtime, installers, tests, examples, and internal workflow references for Codex Pro Bridge.

Start with the repository-level [English README](../README.md) or [中文说明](../README.zh-CN.md).

## Install

Global installation:

```bash
./install.sh --global
```

Windows PowerShell:

```powershell
.\install.ps1 -Global
```

Repository-local installation:

```bash
./install.sh --repo /path/to/repo
```

```powershell
.\install.ps1 -Repo C:\path\to\repo
```

The installer replaces only the ten managed skills and `.shared`. Repository-local installation also adds `.agents/` and `.codex/` to the target repository's local `.git/info/exclude`.

## Entry points

| Need | Skill |
| --- | --- |
| Coordinate Auto Research roles, Research Lines, local Chats, Web conversation lineages, Handoff, and Settlement | `$coordinate-auto-research` |
| Bind, inspect, route, or repair a ChatGPT Project | `$gpt-pro-project-workspace` |
| Normal question or existing conversation | `$gpt-pro-question-window` |
| High-frequency / parallel review of one idea, proposal, or atomic task | `$gpt-pro-review-probe` |
| Deep algorithm, pipeline, or experiment review | `$gpt-pro-research-algorithm-reviewer` |
| Paper framing and reviewer pressure test | `$gpt-pro-paper-brainstormer` |
| Complete external-review loop | `$gpt-pro-algorithm-pipeline` |
| Local experiment matrix | `$experiment-plan-generator` |
| Local implementation/result consistency check | `$implementation-consistency-checker` |

`$gpt-pro-question-window` resolves `local_only`, `standalone`, and `project`
routes automatically. `$bundle-algorithm-context` prepares evidence for
source-backed external rounds. ChatGPT page mutations use Chrome DevTools MCP
by default; the Codex Chrome connector is a pre-submit compatibility fallback.
SSH may host repository or compute work, while one local browser-host dispatcher
stages verified bundles and controls the signed-in Chrome profile.

Windows-native execution and WSL execution with Windows MCP/Chrome are both
supported through optional, environment-backed host configuration. See
[HOST_TOPOLOGIES.md](docs/HOST_TOPOLOGIES.md); the examples do not embed a user
name, drive mapping, or machine-specific path.

For delegated unattended rounds, copy
[`agents/bridge-executor.toml.example`](agents/bridge-executor.toml.example) to
`$CODEX_HOME/agents/bridge-executor.toml` and review its model and service-tier
settings. This is deliberately optional and is never installed over an existing
agent configuration.

See [usage_prompts.md](examples/usage_prompts.md) for invocation examples.
Operational details live in the relevant skills, the
[canonical protocol](.agents/skills/gpt-pro-question-window/references/bridge_protocol.md),
and the
[Project protocol](.agents/skills/gpt-pro-project-workspace/references/project_protocol.md).
