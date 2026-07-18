---
name: gpt-pro-paper-brainstormer
description: Use GPT Pro for research-paper brainstorming, claim sharpening, novelty analysis, related-work positioning, reviewer objections, and experiment story design. Best for algorithm/research ideas where Codex alone is too implementation-biased.
---

# GPT Pro Paper Brainstormer

Gather only the evidence needed to test the research claim, then ask GPT Pro to act as a skeptical research advisor.

## Workflow

1. Clarify the raw idea and target venue level if known.
2. Preview the route through `$gpt-pro-project-workspace`. Preserve the returned
   Project and Thread identity, or use the standalone path when no shared
   Project context is needed.
3. Use `$bundle-algorithm-context` if code, experiments, docs, or logs matter.
4. Read [references/paper_brainstorm_prompt.md](references/paper_brainstorm_prompt.md), then use `$gpt-pro-question-window` to ask GPT Pro.
5. Capture the raw response and record a separate Codex verdict on the same `bridge-thread-id`.
6. Codex synthesizes:
   - Strongest paper claim.
   - Weakest/most vulnerable claim.
   - Required experiments.
   - Whether this is paper-worthy, workshop-worthy, or just engineering.

Completion criterion: claims and related-work references are marked as verified or unverified, the required experiments are concrete, and the publishability judgment is preserved separately from Codex's local verdict.

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
