---
name: gpt-pro-paper-brainstormer
description: Use GPT Pro for research-paper brainstorming, claim sharpening, novelty analysis, related-work positioning, reviewer objections, and experiment story design. Best for algorithm/research ideas where Codex alone is too implementation-biased.
---

# GPT Pro Paper Brainstormer

Use this skill when the user wants paper/research ideation rather than implementation. Codex should gather just enough context, then ask GPT Pro to act as a research sparring partner.

## When to use

- User asks whether an algorithm idea is publishable.
- User wants a sharper paper framing.
- User wants novelty, related work positioning, or reviewer objections.
- User wants benchmark/dataset/eval story design.
- User wants to turn engineering work into a research claim.

## Workflow

1. Clarify the raw idea and target venue level if known.
2. Use `$bundle-algorithm-context` if code, experiments, docs, or logs matter.
3. Use `$gpt-pro-question-window` to ask GPT Pro with the paper prompt.
4. Save the response as the next turn in the current GPT Pro session, using the same `bridge-thread-id` as the bundle.
5. Codex synthesizes:
   - Strongest paper claim.
   - Weakest/most vulnerable claim.
   - Required experiments.
   - Whether this is paper-worthy, workshop-worthy, or just engineering.

## Prompt dimensions

Ask GPT Pro to cover:

- One-sentence paper claim.
- Problem pain and why now.
- Novelty type: algorithm, data, benchmark, system, analysis, application.
- Nearest likely related work clusters.
- What is actually new vs rebranding.
- Strongest title/abstract angle.
- Experimental story.
- Killer baseline and ablations.
- Reviewer objections.
- Minimal evidence needed to make the claim believable.
- A brutally honest publishability judgment.

## Codex-side caution

GPT Pro may invent related work or overstate novelty. After GPT Pro responds, Codex should mark related-work items as unverified unless it has searched or the user supplied citations.
