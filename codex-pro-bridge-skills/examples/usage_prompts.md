# Usage Prompts

## 1. Normal GPT Pro question

```text
Use $gpt-pro-question-window.
Open a new GPT Pro conversation and ask:

[question]

Use bridge thread [bridge-thread-id]. Save the full answer as the next turn in the current GPT Pro session and summarize the actionable parts for me.
```

## 1b. Follow up in an existing GPT Pro session

```text
Use $gpt-pro-question-window.
Reuse bridge thread [bridge-thread-id] and GPT Pro session [gpt-pro-session-id], then ask:

[follow-up question]

Use notes plus the session graph by default; include code files only if they changed or GPT Pro needs to inspect them again.
```

## 2. Deep algorithm review

```text
Use $gpt-pro-research-algorithm-reviewer.
I want a deep algorithm review, not a normal code review.

Goal:
[goal]

Focus files:
[optional paths]

What I am unsure about:
[concerns]

Please ask GPT Pro to evaluate hypothesis, method, baseline, data leakage, reward/loss, evaluation, ablations, novelty, reviewer objections, implementation checkpoints, and go/no-go.
After GPT Pro answers, verify its claims against the repo and give me a minimal experiment plan.
```

## 3. Paper brainstorm

```text
Use $gpt-pro-paper-brainstormer.
I want to turn this algorithm/pipeline idea into a possible paper direction:

[idea]

Please ask GPT Pro to sharpen the one-sentence claim, novelty, related-work pressure, experiment story, reviewer objections, and publishability judgment.
Do not treat related-work names as verified unless they are in the context.
```

## 4. Experiment plan

```text
Use $experiment-plan-generator.
Based on the GPT Pro review at [path] and the current repo, generate a minimal experiment matrix.
Prioritize experiments that separate data effect, objective effect, sampling/filtering effect, and full-method effect.
Include a kill experiment.
```

## 5. Consistency check

```text
Use $implementation-consistency-checker.
Compare the algorithm proposal/review at [path] with the current code, configs, train command, eval command, and latest logs.
Tell me whether the experiment result is trustworthy.
```

## 6. Full pipeline

```text
Use $gpt-pro-algorithm-pipeline.
Run the full Codex → GPT Pro → Codex algorithm review loop for:

[task]

For the first round, bundle only relevant code/docs/configs/logs. For follow-up rounds, prefer notes plus the session graph unless specific files changed. Ask GPT Pro for deep review. Then verify locally, produce experiments, and implement only safe next steps.
Use one bridge thread for the whole loop so follow-up bundles include the task timeline.
```
