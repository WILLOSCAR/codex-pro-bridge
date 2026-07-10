# Bridge Protocol

Read this reference when creating, resuming, persisting, or debugging a Codex Pro Bridge task.

## Canonical identity

Expose one required identifier:

```text
bridge_thread_id = <repo>-<date>-<short-task>
```

Derive endpoint session IDs unless an existing compatible session is explicitly named:

```text
codex_session_id   = <bridge_thread_id>-codex
gpt_pro_session_id = <bridge_thread_id>-gpt-pro
```

Once created, a Codex or GPT Pro session cannot move to another bridge thread. A GPT Pro session also cannot move to another web conversation URL. A mismatch is an error, not a rename.

## Canonical events

The task history contains three normal event types:

1. `codex-snapshot`: immutable Codex notes before an external review round.
2. `gpt-exchange`: the bundle actually sent, prompt, and captured GPT Pro answer.
3. `codex-verdict`: Codex verification, decision, implementation result, tests, and next question.

Bundle construction attempts are local intermediates. Do not add them to the task timeline. The sent bundle is bound to `gpt-exchange` by relative path and SHA-256.

## Storage

```text
.codex/codex-pro-bridge/
  threads/
    index.md
    <bridge-thread-id>.jsonl   # canonical append-only ledger
    <bridge-thread-id>.md      # derived sequence/timeline view
  codex-sessions/
    index.md
    <codex-session-id>/
      session.md
      notes.md                 # current mutable notes
      snapshots/               # immutable historical notes
  gpt-pro-sessions/
    index.md
    <gpt-pro-session-id>/
      session.md
      001-<slug>.md            # immutable raw exchange
      verdicts/                # immutable Codex verdicts
  bundles/                     # immutable evidence artifacts
```

The JSONL ledger is the single source of truth. Markdown timelines, sequence diagrams, and indexes are projections and may be regenerated. Older Markdown-only threads are imported into JSONL on their next write.

Repository-local installation adds `.agents/` and `.codex/` to that repository's local `.git/info/exclude`. Keep bridge state local unless the user explicitly chooses to publish selected artifacts.

All timestamps include a timezone. Event IDs are unique, and each event points to its parent event. Artifact records contain repository-relative paths and SHA-256 digests.

## Round lifecycle

### 1. Snapshot

Write current notes and an immutable snapshot:

```bash
python3 .agents/skills/bundle-algorithm-context/scripts/prepare_codex_session_notes.py \
  --repo . \
  --bridge-thread-id <thread-id> \
  --goal "<goal>" \
  --gpt-pro-question "<question>" \
  --summary-file /tmp/codex-summary.md
```

Completion criterion: `notes.md`, an immutable snapshot, one `codex-snapshot` event, and the Codex session index all exist and agree on the same thread ID.

### 2. Bundle

Build a new artifact. Existing output files are never overwritten:

```bash
python3 .agents/skills/bundle-algorithm-context/scripts/build_algorithm_bundle.py \
  --repo . \
  --bridge-thread-id <thread-id> \
  --goal "<goal>" \
  --question "<question>" \
  --mode algorithm_review \
  --format zip \
  --repo-context auto
```

By default the builder uses the latest immutable Codex notes snapshot recorded in `session.md`, not the mutable `notes.md` pointer.

Completion criterion: the manifest names the correct mode, lists every supplied file, contains no absolute local repository path, passes zip integrity checks, and is small enough to upload. Bundle creation alone does not add a task event.

### 3. Exchange capture

After the answer finishes, immediately capture the raw exchange:

```bash
python3 .agents/skills/gpt-pro-question-window/scripts/save_bridge_turn.py \
  --repo . \
  --bridge-thread-id <thread-id> \
  --web-url https://chatgpt.com/c/... \
  --web-title "<observed title>" \
  --purpose "<task purpose>" \
  --bundle .codex/codex-pro-bridge/bundles/<bundle>.zip \
  --prompt-file /tmp/gpt-pro-prompt.md \
  --answer-file /tmp/gpt-pro-answer.md
```

Completion criterion: a numbered immutable turn exists; its bundle digest matches the file sent; the GPT Pro session remains bound to one thread and one URL; and one `gpt-exchange` event points to the turn.

### 4. Codex verdict

Verify the answer against local files, then record a separate verdict:

```bash
python3 .agents/skills/gpt-pro-question-window/scripts/record_codex_verdict.py \
  --repo . \
  --bridge-thread-id <thread-id> \
  --turn .codex/codex-pro-bridge/gpt-pro-sessions/<session>/001-<slug>.md \
  --summary-file /tmp/codex-summary.md \
  --verification-file /tmp/codex-verification.md \
  --decision-trail-file /tmp/decision-trail.md \
  --tests-file /tmp/tests.md \
  --next-question-file /tmp/next-question.md
```

Completion criterion: an immutable verdict artifact and one `codex-verdict` event point to the captured GPT Pro turn. Never edit the raw GPT Pro answer to add later conclusions.

## Evidence scope

- Default to repository-contained paths.
- Reject missing includes and paths that resolve outside the repository.
- Use `--allow-external-include` only after inspecting and confirming each external file. External archive names are anonymized.
- Include tracked and untracked non-ignored files in auto-selection.
- Fail on high-confidence secret patterns unless a human has reviewed the exact files and explicitly allows them.
- Never rewrite ordinary source contents. Include a file as evidence or omit it.
- Use a safe repository label in uploaded manifests; never expose the absolute local repository path.

## Context policy

- `auto`: first round or implementation-heavy review.
- `explicit`: follow-up round; include only named changed or newly relevant files.
- `none`: reasoning-only follow-up with notes and compact event context.

Keep the full ledger local. Bundles use the latest 24 events and 20,000 characters by default. Increase either limit only when an older event is directly relevant.

## Browser route

Prerequisite: install and enable the Codex Chrome extension. In this environment, use a US-region network node while downloading it from the Chrome Web Store. Then open `chrome://extensions/`, open the extension's **Details**, and enable **Allow access to file URLs**. Without this permission, the local bundle may not be attached.

1. Use signed-in Chrome for ChatGPT/GPT Pro.
2. Verify the Codex extension is enabled and **Allow access to file URLs** remains on.
3. Start the file-chooser wait before clicking the visible upload control.
4. Set the absolute bundle path and verify the attachment chip.
5. Use Computer Use only when Chrome cannot control a native or graphical UI boundary.
6. For a dry run, remove the attachment and verify the composer is empty.

If `setFiles(...)` reports `Not allowed`, enable **Allow access to file URLs** for the Codex Chrome extension. Stop for CAPTCHA, rate limits, abuse warnings, unusual login, passwords, 2FA, or account-security prompts.
