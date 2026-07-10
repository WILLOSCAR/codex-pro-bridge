# Codex Pro Bridge Skills

This package installs seven Codex skills for scoped GPT Pro review and local verification.

## Install

Before using browser upload:

1. Install and enable the Codex Chrome extension. In this environment, use a US-region network node while downloading it from the Chrome Web Store.
2. Open `chrome://extensions/`, open the Codex extension's **Details**, and enable **Allow access to file URLs**.

The local bundle may not be attached when this permission is disabled.

```bash
./install.sh --global
./install.sh --repo /path/to/repo
```

The installer synchronizes only this package's managed skills and hidden shared runtime. It removes stale files inside those directories without touching unrelated skills.

Repository-local installation adds `.agents/` and `.codex/` to the repository's local `.git/info/exclude`.

## Entry points

- Use `$gpt-pro-question-window` for a normal question or existing GPT Pro conversation.
- Use `$gpt-pro-research-algorithm-reviewer` for deep algorithm or pipeline review.
- Use `$gpt-pro-paper-brainstormer` for paper framing.
- Use `$gpt-pro-algorithm-pipeline` for the complete loop.
- Use `$experiment-plan-generator` and `$implementation-consistency-checker` locally when GPT Pro is unnecessary.

Every external round follows:

```text
codex-snapshot -> gpt-exchange -> codex-verdict
```

Use one required `bridge-thread-id`; endpoint session IDs derive from it. JSONL is the canonical append-only ledger, while Markdown and the sequence diagram are derived views. Artifacts are immutable and linked by relative path plus SHA-256.

The bundle builder includes modern Node files such as `.mjs`, keeps definitely-local dependency closures whole before adding auto-selection breadth, and fails closed on omitted explicit evidence. Browser submission uses an exact model/attachment preflight; final handoff uses a parent-and-hash ledger verifier.

Read the full protocol at [.agents/skills/gpt-pro-question-window/references/bridge_protocol.md](.agents/skills/gpt-pro-question-window/references/bridge_protocol.md).

## Validate

```bash
python3 -m unittest discover -s tests -v
python3 tests/validate_skills.py
```

No third-party Python package is required.
