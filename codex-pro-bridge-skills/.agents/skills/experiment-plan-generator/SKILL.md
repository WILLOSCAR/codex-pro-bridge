---
name: experiment-plan-generator
description: Convert an algorithm review or research idea into a prioritized experiment matrix with baselines, ablations, metrics, success/failure criteria, commands, logging requirements, and decision rules. Use after GPT Pro review or before implementing experiments.
---

# Experiment Plan Generator

Use this skill to turn an algorithm idea or GPT Pro review into concrete experiments that Codex can implement or run.

## Inputs

Collect:

- Algorithm goal and hypothesis.
- Current baseline.
- Proposed method variants.
- Available data and split rules.
- Main metric, guardrail metrics, and analysis buckets.
- Compute/time constraints.
- Existing training/eval commands.
- GPT Pro review path if available.

## Required output

Read and fill [references/experiment_matrix_template.md](references/experiment_matrix_template.md).

Also produce:

- Minimal experiment set: smallest set that gives useful decision signal.
- Kill experiment: fastest experiment that can disprove the idea.
- Baseline checklist.
- Ablation checklist.
- Logging checklist.
- Decision rule: Go / iterate / stop.

## Experiment design rules

1. Always include the current baseline.
2. Separate data effect from algorithm effect.
3. Separate objective/reward effect from sampling/filtering effect.
4. Keep same compute/data/model where possible.
5. Add one cheap sanity test before expensive training.
6. Add one failure-mode analysis after metrics.
7. Do not run expensive experiments unless the minimal sanity checks pass.

## Common algorithm experiment patterns

### Data vs objective

- Baseline current data + current objective.
- New data + current objective.
- Current data + new objective.
- New data + new objective.

### Reward / RL / OPD

- SFT baseline.
- Rejection SFT / filtered SFT.
- Preference tuning baseline.
- RL/GRPO/PPO variant.
- OPD variant.
- OPD/RL without filtering.
- OPD/RL with weak teacher removed.
- Criterion-level reward vs scalar reward.

### Agentic QA/search

- Retrieval-only baseline.
- Rewrite-only change.
- Evidence filtering-only change.
- Tool policy-only change.
- Final answer synthesis-only change.
- Full pipeline.
- Failure bucket analysis.

## Codex execution

After generating the matrix, inspect the repo and map each experiment to real files and commands. If commands do not exist, propose the minimal code/config additions to make experiments reproducible.

Completion criterion: every experiment maps to real files/configs/commands or explicitly names missing scaffolding, and the decision rule can be evaluated from logged results.
