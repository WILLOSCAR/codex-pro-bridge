# Bridge Protocol

Read this reference when creating, resuming, persisting, or debugging a Codex Pro Bridge task.

## Execution scopes

Every request resolves to one scope before bridge state is written:

1. `local_only`: Codex completes the task without ChatGPT.
2. `standalone`: one Bridge Thread uses one task-scoped GPT conversation.
3. `project`: one Bridge Thread belongs to the repository's Bridge Project and
   uses a conversation inside its bound ChatGPT Project.

`standalone` is a permanent first-class mode, not an incomplete Project setup.
An existing standalone thread can later be attached to a Project without
rewriting its earlier events.

## Canonical identity

Expose one required task identifier:

```text
bridge_thread_id = <repo>-<date>-<short-task>
```

Project mode adds:

```text
bridge_project_id = <stable-local-project-id>
remote_project_id = <observed-chatgpt-g-p-id>
```

Derive endpoint session IDs unless an existing compatible session is explicitly named:

```text
codex_session_id   = <bridge_thread_id>-codex
gpt_pro_session_id = <bridge_thread_id>-gpt-pro
```

One local repository has at most one Bridge Project, and one Bridge Project has
at most one current ChatGPT Project binding. A Bridge Thread is the Project's
task identity; do not add a parallel Workstream ID.

Once created, a Codex or GPT Pro session cannot move to another bridge thread.
A GPT Pro session also cannot move to another web conversation URL or remote
Project. A mismatch is an error, not a rename.

## Canonical events

The task history contains three normal event types:

1. `codex-snapshot`: immutable Codex notes before an external review round.
2. `gpt-exchange`: the bundle actually sent, prompt, and captured GPT Pro answer.
3. `codex-verdict`: Codex verification, decision, implementation result, tests, and next question.

Bundle construction attempts are local intermediates. Do not add them to the task timeline. The sent bundle is bound to `gpt-exchange` by relative path and SHA-256.

## Storage

```text
.codex/codex-pro-bridge/
  projects/
    index.md
    <bridge-project-id>/
      project.json             # local Project identity
      remote-binding.json      # observed ChatGPT Project binding
      activity.jsonl           # append-only Project audit ledger
      overview.md              # derived Project and task view
      PROJECT_BRIEF.md         # default stable shared source
      sources/
        manifest.json          # observed Project Source state
        plans/                 # immutable upload/removal plans
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

Each thread JSONL ledger is the source of truth for its task history. Project
identity, binding, source state, and task membership live in the Project files
and activity ledger. Markdown timelines, overviews, sequence diagrams, and
indexes are projections and may be regenerated. Older Markdown-only threads
are imported into JSONL on their next write.

Repository-local installation adds `.agents/` and `.codex/` to that repository's local `.git/info/exclude`. Keep bridge state local unless the user explicitly chooses to publish selected artifacts.

All timestamps include a timezone. Event IDs are unique, and each event points to its parent event. Artifact records contain repository-relative paths and SHA-256 digests.

## Round lifecycle

### 0. Resolve the route

Preview a decision before creating notes or bundles:

```bash
python3 .agents/skills/gpt-pro-project-workspace/scripts/resolve_bridge_route.py \
  --repo . \
  --task "<decision or deliverable>" \
  --external-reasoning
```

Codex substitutes `--local-only` when no external round is useful. For
`local_only`, stop the external workflow. For a ready Project decision,
rerun with `--apply` once to attach or reuse its Bridge Thread. If the decision
requires confirmation, create, bind, or verify the Project first. Do not
silently fall back to standalone when this repository already has a Bridge
Project with a stale or unverified binding.

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

In Project mode, add `--bridge-project-id <project-id>` to snapshot, bundle,
exchange-capture, and verdict commands.

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

For `auto`, use any `--include` paths as required focus seeds, close their definitely-local source dependencies before adding breadth, and fail if the required closure exceeds `--max-files`. For `explicit`, require at least one include and fail when any requested file is filtered or omitted; `--allow-incomplete-includes` is an audited escape hatch, not a default. Modern Node evidence includes `.mjs`, `.cjs`, `.mts`, and `.cts`. For `none`, `--max-files 0` is valid.

### 2.5 Browser preflight

After the visible attachment chip and exact selected model label are observable, gate submission:

```bash
python3 .agents/skills/gpt-pro-question-window/scripts/check_browser_preflight.py \
  --requested-model Pro \
  --selected-ui-label '<exact visible label>' \
  --bundle /absolute/path/to/bundle.zip \
  --attachment-name '<visible filename>' \
  --upload-control visible-menu
```

Only click Send when this command succeeds. A subscription/account label does not establish the selected model. `极高` and `Pro` are distinct labels.

For Project mode, also supply `--expected-project-id`,
`--observed-project-id`, `--expected-workspace`, `--observed-workspace`,
`--expected-account-label`, `--observed-account-label`, and
`--binding-status active`. Expected values come from routing; observed values
come from the visible destination, not from a same-titled sidebar item.

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
  --requested-model Pro \
  --selected-ui-label '<exact visible label>' \
  --attachment-name '<visible filename>' \
  --upload-control visible-menu \
  --submitted-at '<ISO-8601 with timezone>' \
  --generation-observed-at '<ISO-8601 with timezone>' \
  --response-completed-at '<ISO-8601 with timezone>' \
  --prompt-file /tmp/gpt-pro-prompt.md \
  --answer-file /tmp/gpt-pro-answer.md
```

Capture the raw answer even when the observed model is mismatched or unverified, but preserve that status and do not claim the answer came from Pro.

For Project mode, also pass `--bridge-project-id <project-id>`,
`--remote-project-id <g-p-id>`, `--observed-workspace <workspace>`, and
`--observed-account-label <account-label>`.

Completion criterion: a numbered immutable turn exists; its bundle digest matches the file sent; model and attachment provenance are recorded truthfully; the GPT Pro session remains bound to one thread and one ChatGPT URL; and one `gpt-exchange` event points to the turn.

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

### 5. Verify the thread

Before another round and before final handoff, verify the append-only chain and every referenced artifact:

```bash
python3 .agents/skills/gpt-pro-question-window/scripts/verify_bridge_thread.py \
  --repo . \
  --bridge-thread-id <thread-id> \
  --require-complete-rounds
```

The verifier fails on broken parents, duplicate identities, unsafe or missing artifact paths, artifact or bundle hash mismatches, invalid ordering, and incomplete final rounds.

Project mode also requires:

```bash
python3 .agents/skills/gpt-pro-project-workspace/scripts/verify_bridge_project.py \
  --repo . \
  --bridge-project-id <project-id> \
  --require-active-binding
```

## Evidence scope

- Default to repository-contained paths.
- Reject missing includes and paths that resolve outside the repository.
- Use `--allow-external-include` only after inspecting and confirming each external file. External archive names are anonymized.
- Include tracked and untracked non-ignored files in auto-selection.
- Fail on high-confidence secret patterns unless a human has reviewed the exact files and explicitly allows them.
- Never rewrite ordinary source contents. Include a file as evidence or omit it.
- Use a safe repository label in uploaded manifests; never expose the absolute local repository path.

## Context policy

- `auto`: first round or implementation-heavy review. Rank focus, then keep its definitely-local dependency closure whole before adding breadth.
- `explicit`: follow-up round; include only named changed or newly relevant files. Every requested safe file must be included unless the operator deliberately uses the incomplete-evidence override.
- `none`: reasoning-only follow-up with notes and compact event context.

Keep the full ledger local. Bundles use the latest 24 events and 20,000 characters by default. Increase either limit only when an older event is directly relevant.

Project Sources are durable context shared across Project conversations. Task
Bundles are immutable, round-scoped evidence. Do not upload volatile diffs and
logs as Project Sources, and do not use Project Sources as a substitute for
recording exactly what one review round saw.

## Browser route

Prerequisite: install and enable the Codex Chrome extension. In this environment, use a US-region network node while downloading it from the Chrome Web Store. Then open `chrome://extensions/`, open the extension's **Details**, and enable **Allow access to file URLs**. Without this permission, the local bundle may not be attached.

1. Use signed-in Chrome for ChatGPT/GPT Pro.
2. Verify the Codex extension is enabled and **Allow access to file URLs** remains on.
3. Start the file-chooser wait before clicking the visible attachment button and visible upload menu item. Never directly click hidden `#upload-files`.
4. Set the absolute bundle path and verify the attachment chip.
5. Read the exact selected model label and run `check_browser_preflight.py`; do not send on mismatch.
6. In Project mode, open the saved Project URL and verify its visible ID,
   account/workspace, and active local binding before creating or reusing a
   conversation.
7. Use Computer Use only when Chrome cannot control a native or graphical UI boundary.
8. For a dry run, remove the attachment and verify the composer is empty.

While generation remains visibly active, keep waiting without resubmission. Inspect every 30–60 seconds, provide a short progress update at least once per minute, and record only timestamps actually observed. On a stalled or failed state, capture diagnostics and stop instead of duplicating the request.

If `setFiles(...)` reports `Not allowed`, enable **Allow access to file URLs** for the Codex Chrome extension. Stop for CAPTCHA, rate limits, abuse warnings, unusual login, passwords, 2FA, or account-security prompts.
