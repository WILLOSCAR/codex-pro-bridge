---
name: implementation-consistency-checker
description: Verify that an algorithm proposal, GPT Pro review, code implementation, configs, data splits, eval scripts, logs, and experiment commands are mutually consistent. Use before trusting results or after Codex implements an algorithm/pipeline change.
---

# Implementation Consistency Checker

Use this skill when the risk is that the algorithm described in the plan is not the algorithm actually implemented or evaluated.

This skill should usually be performed by Codex locally. GPT Pro is optional; do not call GPT Pro unless the mismatch is conceptual and needs outside reasoning.

## Inputs

Collect:

- Proposal/spec/review document.
- Changed code files.
- Config files used for experiments.
- Training command.
- Evaluation command.
- Logs/result files.
- Data split definitions.

## Required checks

### 1. Proposal ↔ code

- Does the implemented loss/reward match the proposal?
- Is the sign of the objective correct?
- Are coefficients, thresholds, temperatures, weights, and normalization correct?
- Are all proposed components implemented?
- Are any extra undocumented tricks enabled?

### 2. Code ↔ config

- Does the config enable the intended method?
- Are defaults overriding experiment settings?
- Are ablation flags actually connected to code paths?
- Are model/data/checkpoint paths correct?

### 3. Config ↔ command

- Does the run command load the intended config?
- Does CLI override config values?
- Are seed, split, checkpoint, and output path explicit?

### 4. Train ↔ eval

- Is eval using the intended checkpoint?
- Is eval using the correct split and metric?
- Are train/test/validation splits isolated?
- Is there user/session/query/time leakage?

### 5. Logging ↔ metric

- Does the logged metric name correspond to the actual formula?
- Are averages, buckets, and denominators correct?
- Are failed/timeout/skipped examples included consistently?
- Are case-level outputs saved for debugging?

### 6. Ablation validity

- Does each ablation disable only the intended component?
- Are data, compute, model, seed, and eval conditions otherwise matched?
- Is there contamination from cached artifacts?

## Output format

```text
# Implementation Consistency Report

## 1. Checked Inputs
- Proposal/review:
- Code files:
- Configs:
- Commands:
- Logs/results:

## 2. Matched
Items that are consistent.

## 3. Mismatched
Concrete mismatches with file/line references when possible.

## 4. Unclear / Needs User Decision
Ambiguities or missing evidence.

## 5. Experiment Validity Risk
Whether current results can be trusted.

## 6. Required Fixes
Prioritized code/config/eval/logging fixes.

## 7. Re-run Plan
Commands or steps to rerun after fixes.
```

## Severity labels

- P0: invalidates experiment result.
- P1: likely changes conclusion.
- P2: affects reproducibility or interpretation.
- P3: cleanup/documentation.

## Implementation guidance

When possible, use exact file paths and line references. If line references are not available, quote function/class/config keys. Prefer small fixes and sanity tests before larger refactors.
