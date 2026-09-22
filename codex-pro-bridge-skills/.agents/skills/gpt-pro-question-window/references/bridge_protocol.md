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

For a new Executor round, the parent freezes inputs with
`scripts/prepare_bridge_execution.py`. This entry point is the owner of route/store
composition: it derives the Thread ID, applies an explicitly requested Project rebind,
and publishes an atomic request plus `executor_handoff/v2`. Callers must not supply a
guessed Thread ID or hand-write a second request. Without an explicit target Project,
the current local binding is reused and its existing verification gates remain active.

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
  executor-preparations/<thread>/
                                # atomic request/handoff/receipt before Executor spawn
  attempts/<thread>/<id>.json   # durable submission/wait checkpoint
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
python3 ${CODEX_HOME:-$HOME/.codex}/skills/gpt-pro-project-workspace/scripts/resolve_bridge_route.py \
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

Exit codes: `0` = ready (attached or clean), `3` = needs human confirmation
(the printed JSON lists `requires_confirmation`), `2` = bad invocation.
Treat `3`, not `2`, as the confirmation signal.

### High-frequency Review Probes

For repeated, fine-grained, parallel review of one idea, proposal, or atomic
sub-task, use standalone Review Probes instead of Project rounds. A probe never
attaches to a Bridge Project, so it never consults Project source-sync and is
never blocked when unrelated shared sources are stale. See
[../../gpt-pro-review-probe/SKILL.md](../../gpt-pro-review-probe/SKILL.md). Many
probes run in parallel because each is a distinct thread with its own ledger
and conversation-scoped browser claim; only the same conversation or shared
Project mutation conflicts.

### 1. Snapshot

Write current notes and an immutable snapshot:

```bash
python3 ${CODEX_HOME:-$HOME/.codex}/skills/bundle-algorithm-context/scripts/prepare_codex_session_notes.py \
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
python3 ${CODEX_HOME:-$HOME/.codex}/skills/bundle-algorithm-context/scripts/build_algorithm_bundle.py \
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

An existing chat acquires a host-local conversation claim and verifies its exact
conversation URL. A new Project chat acquires `--scope project --bootstrap`; a
new standalone chat acquires `--scope profile --bootstrap`. Bootstrap returns a
tab owner token but creates no durable conversation binding yet. Pass the full
`list_pages` result to `resolve-tab`, then read `sessionStorage` only on requested
pageIds and resolve again. It alone counts matches and permits a canonical new
tab only while unbound and no matching owner/home exists. After an owner write,
resolve again; only an observed claim owner records `tab_bound`. Owner mismatch,
missing bound owner, duplicate owner, and wrong owner URL HOLD. A bootstrap owner
on the same Project's `/c/<id>` is promotion-ready. Never use `localStorage`,
unknown query/fragment bootstrap URLs, or `close_page`. For a bundle round, stage the
bundle and upload only the returned G-drive Windows path. The Chrome DevTools upload
sequence is one click on the visible Add files control, then a fresh same-page snapshot,
then a direct `upload_file` call on the fresh **Upload from computer** menu-item UID;
persist its frozen action plan and successful normalized result receipt beside the round
artifacts. Never click that menu item first or click Add files again when a chip is missing.

If `upload_file` fails/returns an unknown result or the attachment chip is absent, treat
the state as possible native chooser residue on Windows: stop upload and Send, do not claim
browser UI cleanup, and clean the exact G-drive staged file separately. A DevTools preflight
also requires the persisted success receipt to bind the same plan, page, staged path, digest,
filename, and chip; a missing, blocked, or stale receipt fails closed. The connector
fallback's `waitForEvent("filechooser")` plus menu-item click is a different route and
must not be mixed into the DevTools sequence.

Project 首页的精确 `?tab=chats` 属于允许的展示参数。多个可用首页由 resolver 自动选择一页；
已有唯一 owner 优先，其他页保持原样。候选 URL 总数与唯一 owner 数分别记录，重复同一 owner
仍视为歧义。原始 MCP 格式转换与预检参数由 browser adapter 的统一观察入口负责。

尚待永久绑定的 bootstrap 身份保存在宿主 registry 的 `pending_bootstraps` 中，独立于
短期 lease。过期重领沿用原 owner 和 `tab_bound`；其他线程不能回收 pending owner。
只有同页提升成功或经过清理核对的显式释放才退役该记录。旧 registry 中仍存在的 bootstrap
lease 可在读取/重领时恢复该身份；已被旧代码丢弃的记录不能凭空重建。

Legacy `chrome-default` and `chrome-devtools` registry rows alias to
`chrome-stable-default` during lookup without rewriting history. An exact bound
conversation may return `replace-legacy-owner-token` to consolidate its sole
observed older token to the newest binding token; observing two compatible owner
tokens at once is `duplicate-owner` and HOLDs.

For `explicit`/`auto`, after the visible attachment and model controls appear, take a
fresh snapshot on the same pageId and run `check_browser_preflight.py`. Existing chats pass both
conversation IDs and their saved turn boundary. Bootstrap passes
`--conversation-bootstrap`, leaves both conversation IDs empty, and uses the
literal boundary `new-conversation`. Both paths pass exact host, URL, match-count,
pageId, owner-token, model, attachment, and prompt SHA-256 observations.

For `none`, no attachment is expected or staged; run the same fresh model, page, and
prompt observations without requiring an attachment observation. The Send and capture
gates remain unchanged.

Only click Send when this command succeeds. Read the checked model item and
thinking strength as separate observations. For a named model, use
`--model-selection-kind exact`. For the dynamic `最新` choice, request and record
that exact visible alias with `--model-selection-kind latest-alias`; this proves
only that the alias was selected and must not be reported as verified Astra.
Record the complete visible thinking-strength value, including its numeric level
when present (for example `6 Pro`), and require an exact match. A strength or
account label containing `Pro` does not establish the model. Project
mode also supplies exact expected/observed Project, workspace, account, and
active-binding values. Page title, sidebar position, and selected-page state are
never identity.

Treat model and thinking controls as one bounded pre-Send transaction: one combined
initial read, only the actions required by a mismatch, and one combined final
confirmation. Persist `model-controls/v1` and pass its validated receipt to browser
preflight on the DevTools route. A successful preflight freezes these observations;
waiting, promotion, recovery, and capture must not reopen or reconfirm the controls.

### 2.6 Submission handoff and response watcher

For an existing chat, use the stable ChatGPT conversation ID exposed by Codex's
native chat reference or the verified conversation URL before Send. An
`@`-mentioned chat supplies this identity; it is not a completion subscription.
Titles are display metadata, not identity. Record its existing turn IDs or cursor
as the pre-submit boundary. Bootstrap instead records `new-conversation` and
obtains the conversation ID only through post-Send promotion. Both paths keep a
digest of the exact prompt.

Hold an existing conversation claim only through exact-tab discovery, the applicable
upload (bundle rounds only), preflight, and one Send. For bootstrap, hold the broader claim after the first
accepted Send until the same owner token and pageId expose exactly one new
conversation URL. Take a fresh snapshot and run `promote-bootstrap`; it atomically
narrows the live claim and creates the permanent Bridge Thread-to-conversation
binding. Only then release with `send-accepted` and clean the exact staged file.
If post-Send identity is missing or ambiguous, preserve the claim and do not
resend. Do not hold a successfully promoted claim while ChatGPT generates.

只有当前工具确实支持读取 ChatGPT 网页对话时才优先使用原生 `read_thread`；否则使用下面的浏览器读取路径：

1. Read the exact ChatGPT conversation ID, never a same-titled chat.
2. Identify the new user turn after the saved boundary and verify it against the
   submitted prompt or its unique fingerprint. Pin that remote turn ID—the ID
   of the turn containing the target user message and its direct assistant
   reply; never use "the latest turn" as the sole selector.
3. Accept only that turn's direct assistant reply when the turn status is
   `completed`, no error is present, and the returned assistant item is not
   marked `truncated`.
4. If the native tool is unavailable, the target is ambiguous, or any required
   item is truncated, reacquire the same conversation claim, rediscover the tab
   by durable owner token plus exact URL, require one unique matching tab,
   obtain a fresh pageId/snapshot, and prove that the exact user-turn ID lies after the saved boundary and matches
   the prompt SHA-256 before copying its pinned reply. Pass those tab
   observations and the same remote turn ID to persistence, then release. Never
   substitute the page's bottom-most response.

发送检查点、能力检测、等待及 watcher 登记的可执行流程统一见
[attempt_recovery.md](attempt_recovery.md)。每轮在预检后创建 attempt，点击前保存
`send-started`，用相同 owner 核对发送后固定的用户 turn。预检与恢复入口会拒绝对未解决发送的重试。
仅有当前任务唤醒工具且实际登记成功时才使用后台 watcher；否则保持活跃线程有界读取。
等待不产生第四种 canonical event；完整回答捕获前不能追加 `gpt-exchange`。
明确失败或已到显式业务截止时间的轮次仍不完整，`--require-complete-rounds` 必须继续失败；
仅等待批次、工具或宿主运行上限产生的 `pending` 必须续接同一 attempt，不能据此把网页轮次标为失败。

### 3. Exchange capture

After the full answer is available, immediately capture the raw exchange:

```bash
python3 ${CODEX_HOME:-$HOME/.codex}/skills/gpt-pro-question-window/scripts/save_bridge_turn.py \
  --repo . \
  --bridge-thread-id <thread-id> \
  --web-url https://chatgpt.com/c/... \
  --web-title "<observed title>" \
  --purpose "<task purpose>" \
  --bundle .codex/codex-pro-bridge/bundles/<bundle>.zip \
  --requested-model '最新' \
  --selected-ui-label '最新' \
  --model-selection-kind latest-alias \
  --requested-thinking-intensity '6 Pro' \
  --selected-thinking-intensity '6 Pro' \
  --attachment-name '<visible filename>' \
  --upload-control visible-menu \
  --submitted-at '<ISO-8601 with timezone>' \
  --generation-observed-at '<ISO-8601 with timezone>' \
  --response-completed-at '<ISO-8601 with timezone>' \
  --capture-route native-read-thread \
  --answer-format native-raw \
  --attempt-id '<attempt-id>' \
  --remote-turn-id '<matched completed turn id>' \
  --prompt-file /tmp/gpt-pro-prompt.md \
  --answer-file /tmp/gpt-pro-answer.md
```

For native capture, omit browser claim and tab observations. For browser
fallback capture, pass `--capture-route browser-fallback`, the newly acquired
claim token, exact page URL/id, fresh snapshot pageId, tab owner token, and the
same remote turn ID. Capture the pinned reply through the visible Copy reply
control and pass `--answer-format copied-markdown`; release afterward. If only
plain text is available, pass `plain-text-degraded` and do not describe it as
lossless. Never save a truncated native item as the raw answer.

Capture the raw answer even when the observed model is mismatched, unverified,
or selected through a dynamic alias. Preserve that status and do not claim an
alias-selected answer came from Astra without separate exact model evidence.

For Project mode, also pass `--bridge-project-id <project-id>`,
`--remote-project-id <g-p-id>`, `--observed-workspace <workspace>`, and
`--observed-account-label <account-label>`.

Completion criterion: a numbered immutable turn exists; its bundle digest matches the file sent; its capture route and target remote turn are recorded; model and attachment provenance are recorded truthfully; the GPT Pro session remains bound to one thread and one ChatGPT URL; and one `gpt-exchange` event points to the turn.

### 4. Codex verdict

Verify the answer against local files, then record a separate verdict:

```bash
python3 ${CODEX_HOME:-$HOME/.codex}/skills/gpt-pro-question-window/scripts/record_codex_verdict.py \
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
python3 ${CODEX_HOME:-$HOME/.codex}/skills/gpt-pro-question-window/scripts/verify_bridge_thread.py \
  --repo . \
  --bridge-thread-id <thread-id> \
  --require-complete-rounds
```

The verifier fails on broken parents, duplicate identities, unsafe or missing artifact paths, artifact or bundle hash mismatches, invalid ordering, and incomplete final rounds.

Project mode also requires:

```bash
python3 ${CODEX_HOME:-$HOME/.codex}/skills/gpt-pro-project-workspace/scripts/verify_bridge_project.py \
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

The deterministic `prepare_bridge_execution.py` entry point freezes this policy in both
the request and `executor_handoff/v2`. `explicit` requires at least one `--file`,
`none` forbids every `--file` and freezes `--max-files 0`, while `auto` permits an
empty focus list but freezes a positive `--max-files` limit. Contradictory inputs fail
before an explicit Project rebind. A `none` handoff omits the
`upload-task-bundle` action and does not create, stage, or upload an attachment; the
Executor may still send and capture the authorized round.

Keep the full ledger local. Bundles use the latest 24 events and 20,000 characters by default. Increase either limit only when an older event is directly relevant.

Project Sources are durable context shared across Project conversations. Task
Bundles are immutable, round-scoped evidence. Do not upload volatile diffs and
logs as Project Sources, and do not use Project Sources as a substitute for
recording exactly what one review round saw.

## Browser route

Read [browser_adapters.md](browser_adapters.md) and use Chrome DevTools MCP for each browser-mutating critical section. The Codex Chrome connector is a compatibility fallback only when DevTools MCP is unavailable or fails before upload/Send while the composer remains empty. The connector alone requires its extension and **Allow access to file URLs** permission.

1. Use signed-in Chrome for ChatGPT/GPT Pro.
2. Acquire a conversation claim for an existing chat, or a Project/profile bootstrap claim for a new chat.
3. Pass the complete `list_pages` result to `resolve-tab`, read only requested owners, and follow only its action. Existing conversations require owner plus exact URL; only an unbound bootstrap `open-canonical-tab` action permits a new page. Never select by title or current page and never close pages.
4. Stage with `manage_browser_staging.py`, upload only its G-drive Windows path through a visible control, and verify the exact attachment. On the DevTools route, persist both the frozen `devtools-upload/v1` action plan and the successful normalized upload receipt; a plan without its accepted receipt is not a valid preflight.
5. Take a fresh same-page snapshot, read the exact model controls, and run `check_browser_preflight.py`; DevTools bundle rounds must pass both the repo-local action plan and its success receipt, while connector fallback rounds do not use those DevTools artifacts. Bootstrap uses `new-conversation` and no conversation ID.
6. In Project mode, verify visible Project ID, account/workspace, and active binding.
7. Use Computer Use only when neither browser route can control a native or graphical UI boundary.
8. For a bootstrap dry run or pre-Send failure, remove the attachment, verify the composer is empty, clear only this claim's exact `sessionStorage` owner token, and release with exact-file cleanup plus the bootstrap cleanup attestation. If no prepare action reached a page, attest that the token was never bound.

For bootstrap, promote the exact post-Send conversation URL before release.
Release the claim and clean the staged file after Send is visibly accepted and,
when applicable, promotion succeeds.
Observe generation through the native response handoff; do not resubmit. After a
reload, reacquire the same claim, rerun full-list `resolve-tab`, and run
`check_browser_recovery.py` with the complete page/owner JSON and a fresh resolved pageId/snapshot. A bound bootstrap missing its owner HOLDs; a promotion-ready page promotes without resend. Reacquire only for a
required full-answer fallback. On a stalled or failed state, capture diagnostics
and stop instead of duplicating the request.

An owner/URL HOLD is an observation conflict, not proof that the page returned to a
Project home. Do not navigate or reload to make observations fit. Re-read the live URL
and owner on the same pageId; state a navigation only when direct same-page observations
prove the transition.

If DevTools MCP fails before any upload action/chooser and the composer remains empty, the connector may be tried once. Do not switch routes after `upload_file` has been invoked or its chip/chooser outcome is unknown. A ChatGPT service rejection is not a browser-route failure. Stop for CAPTCHA, rate limits, abuse warnings, unusual login, passwords, 2FA, remote-debugging permission, or account-security prompts.

The browser registry is host-local across repositories/worktrees. Different
conversation claims may coexist; the same conversation and shared Project
mutations conflict. MCP page IDs remain instance-local and are always paired
with exact URL plus durable tab owner token. Windows MCP and Chrome share the
G-drive staging root; a remote Codex process cannot use `--autoConnect` to
discover the operator's local Chrome.
