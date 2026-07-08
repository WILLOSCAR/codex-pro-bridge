---
name: bundle-algorithm-context
description: Build a compact direct-evidence algorithm context bundle for GPT Pro from code, configs, docs, experiment logs, metrics, and user notes while excluding env, credential, database, raw data, and large artifact files. Use before GPT Pro algorithm review, paper brainstorm, experiment analysis, or implementation consistency work.
---

# Bundle Algorithm Context

Use this skill when a task needs GPT Pro to evaluate an algorithm, research idea, experiment, training/eval pipeline, or implementation. The goal is not to dump the repository. The goal is to create the smallest useful direct-evidence bundle.

## Bundle principle

Include only evidence relevant to the decision:

- Bridge thread context: a compact recent window from the task-level ledger linking Codex updates, bundles, and GPT Pro turns.
- Codex session notes: detailed local conversation summary plus recent raw turns when available.
- Problem definition and user goal.
- Current algorithm / method / hypothesis, when GPT Pro needs implementation evidence for this round.
- Data pipeline and label construction.
- Training loop, reward/loss, sampling, filtering, config.
- Evaluation scripts, metrics, judge prompts, result tables, logs.
- Baselines and ablations.
- Changed files and recent diffs.
- Explicit user questions for GPT Pro.

Exclude `.env` files, credential/key/cookie files, databases, generated artifacts, large binaries, raw data dumps, vendor folders, and unrelated implementation files. Do not rewrite normal source/config/doc/log content just because it contains words such as `token` or `password`; prefer file-level exclusion and user confirmation for uncertain cases.

## Bundle artifact shape

For code or implementation evidence, prefer a zip upload package:

```text
.codex/codex-pro-bridge/bundles/YYYYMMDD-HHMMSS-algorithm-context.zip
  README_FOR_GPT_PRO.md
  source/
  context/
```

`README_FOR_GPT_PRO.md` is the manifest: task, evidence boundary, file list, and requested output. Source/config/docs stay as files under `source/`. Codex notes, compact thread context, git status, and extra notes stay under `context/`.

Use a single markdown bundle only when the evidence is already very small or when file upload is unavailable and the user explicitly approves that fallback. Do not paste a whole code bundle into ChatGPT as a workaround.

## Codex session notes

Use one `bridge-thread-id` for the whole task. Before bundling, create Codex-side notes under:

```text
.codex/codex-pro-bridge/codex-sessions/<codex-session-id>/notes.md
```

These notes are required by default. Creating notes appends a `codex-update` event to `.codex/codex-pro-bridge/threads/<bridge-thread-id>.md`. The thread file keeps a Mermaid `gitGraph` view plus the full local ledger. The bundle includes a compact recent thread window so follow-up GPT Pro rounds can see the task history without pasting every old answer. Notes should be detailed enough for GPT Pro to understand the local reasoning path:

- User goal and current task.
- Current hypothesis or algorithm idea.
- Important context from this Codex thread.
- Decisions already accepted.
- Ideas rejected or deferred.
- Open questions and uncertainty.
- Code/files already inspected.
- What GPT Pro should focus on.
- Recent raw conversation: roughly the last 10 relevant user/assistant turns when Codex can access them.

If the runtime cannot expose raw current-session history to a script, Codex should write the summary from visible conversation context and state that raw history was unavailable. Do not invent missing turns.

## Workflow

1. Identify review mode:
   - `algorithm_review`
   - `experiment_analysis`
   - `paper_brainstorm`
   - `implementation_check`
   - `general_question`
2. Inspect repo structure with `git status`, `git diff --stat`, and file listing.
3. Create or update Codex session notes with `scripts/prepare_codex_session_notes.py` or by writing `notes.md` directly.
4. Identify focus files from user-specified paths, changed files, likely algorithm files, config files, docs, eval scripts, and logs.
5. Choose repository file policy:
   - `--repo-context auto` for the first evidence-heavy round or when implementation details matter.
   - `--repo-context explicit` for follow-up rounds; include files only when listed with `--include`.
   - `--repo-context none` for pure reasoning follow-ups.
6. Run this skill's bundling script. For ChatGPT upload, use `--format zip` unless the evidence is tiny.
7. Read the generated bundle and verify:
   - It states the task and question clearly.
   - It includes Codex session notes by default.
   - It includes compact bridge thread context when available.
   - It has an explicit evidence boundary listing what GPT Pro can and cannot assume was supplied.
   - It includes only relevant files under `source/` when using zip.
   - It excludes obvious env, credential, database, raw-data, large artifact, and static/binary files.
   - It is small enough to upload.
8. If upload or GPT Pro document reading is slow, do not paste the full bundle. Regenerate with `--repo-context explicit`, fewer `--include` paths, lower `--max-files`, or split into at most two or three focused attachments such as manifest/notes, frontend snippets, and API/contracts snippets.

## Script usage

From the repository root, run the script from this skill directory. For repo-local install, this command works:

```bash
python3 .agents/skills/bundle-algorithm-context/scripts/prepare_codex_session_notes.py \
  --repo . \
  --bridge-thread-id "<bridge-thread-id>" \
  --codex-session-id "<codex-session-id>" \
  --goal "<user goal>" \
  --gpt-pro-question "<question for GPT Pro>" \
  --summary-file /tmp/codex-session-summary.md \
  --raw-history-file /tmp/recent-codex-turns.md

python3 .agents/skills/bundle-algorithm-context/scripts/build_algorithm_bundle.py \
  --bridge-thread-id "<bridge-thread-id>" \
  --codex-session-id "<codex-session-id>" \
  --goal "<user goal>" \
  --question "<question for GPT Pro>" \
  --mode algorithm_review \
  --format zip \
  --repo-context auto \
  --max-thread-events 24 \
  --max-thread-chars 20000 \
  --out .codex/codex-pro-bridge/bundles/algorithm-context.zip
```

If the user provides focus paths, pass them explicitly:

```bash
python3 .agents/skills/bundle-algorithm-context/scripts/build_algorithm_bundle.py \
  --bridge-thread-id "<bridge-thread-id>" \
  --codex-session-id "<codex-session-id>" \
  --goal "<user goal>" \
  --question "<question for GPT Pro>" \
  --mode algorithm_review \
  --format zip \
  --repo-context explicit \
  --max-thread-events 24 \
  --max-thread-chars 20000 \
  --include src/train.py configs/reward.yaml docs/experiment.md results/latest.md
```

Keep the full bridge thread locally, but keep bundles bounded. Defaults include the latest 24 thread events and cap thread context at 20,000 characters. Increase these only when older events are directly needed by GPT Pro.

For follow-up rounds, do not resend repository files by default. Prefer `--repo-context explicit` without `--include`; this sends Codex notes plus session graph/context. Add a small `--include` list only for files that changed or need a fresh GPT Pro read.

For frontend-heavy reviews, include web entry files such as `index.html`, `.css`, `.js`, `.ts`, and API boundary files. The bundler includes common web source extensions, but still verify the zip file list before upload.

If installed globally, locate this skill's directory from the loaded skill path and run its script by absolute path.

## What to include for algorithm review

Prioritize:

- `README`, `AGENTS.md`, architecture docs, experiment docs.
- Training scripts and config files.
- Dataset preprocessing and label construction code.
- Reward/loss/model code.
- Evaluation scripts and judge prompts.
- Result summaries, tables, and relevant logs.
- Recent diff if the task concerns a new change.

## Exclusion requirements

Before sending to GPT Pro, make sure the bundle does not contain:

- `.env` or environment files.
- API keys, access tokens, cookies, session IDs.
- SSH keys, certificates, private keys.
- Raw user data or large sensitive datasets.
- Database connection strings.
- Internal credentials.

The bundler should not rewrite normal included file contents. If file-level exclusion is uncertain, ask the user before uploading.
