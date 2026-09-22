# Bridge Executor Handoff

This is the shared boundary between the main Codex agent and the global
`bridge_executor` Agent. It defines the stable input, ownership, continuation, and
receipt contract. The evolving command-line, browser, and ledger algorithms remain in
`bridge_protocol.md`, `browser_adapters.md`, `attempt_recovery.md`, and the existing
Python helpers.

## Purpose and authority

The main agent decides whether an external round is useful, freezes the question and
evidence scope, authorizes external actions, and later verifies the saved answer. The
Executor performs one mechanical Bridge round inside that frozen boundary. Neither the
Agent TOML nor this handoff document is a second owner of browser identity, attempts,
or ledger state.

Read this document together with the three canonical references before any action:

- [Bridge Protocol](bridge_protocol.md)
- [Browser Adapters](browser_adapters.md)
- [发送检查点与等待恢复](attempt_recovery.md)

## Handoff format

The parent supplies one machine-readable file or an equivalent structured message. The
new-round shape is JSON with `schema_version = "executor_handoff/v2"`. The older v1
shape remains readable only for `recover-only` continuation of an already-created
attempt:

```json
{
  "schema_version": "executor_handoff/v2",
  "mode": "prepare-and-run",
  "repo": "/absolute/repository/path",
  "bridge_thread_id": "repo-date-task",
  "bridge_project_id": "local-project-id",
  "remote_project_id": "g-p-id",
  "target_project_url": "https://chatgpt.com/g/g-p-id/project",
  "conversation_url": "https://chatgpt.com/c/exact-id",
  "binding_action": "reuse",
  "request_file": "/absolute/path/request.json",
  "request_sha256": "sha256-of-request.json",
  "business_deadline": null,
  "allow_send": true,
  "allowed_external_actions": ["upload-task-bundle", "send-once", "capture-reply"],
  "expected_output_dir": "/absolute/path/for/bridge/artifacts",
  "context_policy": "explicit",
  "max_files": 1,
  "attachment_policy": "bundle",
  "requested_model": "最新",
  "model_selection_kind": "latest-alias",
  "requested_thinking_intensity": "高"
}
```

Required fields for every handoff are `schema_version`, `mode`, `repo`,
`bridge_thread_id`, `request_file`, `request_sha256`, `allow_send`,
`allowed_external_actions`, and `expected_output_dir`. `business_deadline` is optional
and may be explicitly `null`; when omitted or null, the round has no business
termination time. `bridge_project_id` and `remote_project_id` are required when the
route is Project-scoped. A v2 `prepare-and-run` handoff must also include non-empty
`requested_model`, `model_selection_kind` (`exact` or `latest-alias`),
`requested_thinking_intensity`, `target_project_url` when Project-scoped, and
`binding_action` (`none`, `reuse`, `verify`, or `rebind-and-verify`). An existing
conversation handoff must include its exact `conversation_url`; a new conversation
must instead carry the already-resolved bootstrap scope and expected Project/profile
identity, as permitted by the canonical browser protocol. `attempt_id` is required for
`recover-only` and must be absent for a new `prepare-and-run` attempt. The
deterministic parent entry point is `scripts/prepare_bridge_execution.py`; the parent
must pass its published handoff and must not hand-write a Thread ID or a second
model-control copy in the request.

The v2 `prepare-and-run` handoff requires and freezes `context_policy` (`auto`,
`explicit`, or `none`), its `max_files` limit, and the derived `attachment_policy`
(`bundle` or `none`). `explicit`
requires a non-empty closed `files` list and freezes its count; `auto` may use an empty
focus list but requires a positive limit; `none` requires an empty list and
`max_files = 0`. Contradictory combinations fail before any Project rebind. A `none`
round authorizes `send-once` and `capture-reply` but never `upload-task-bundle`; its
preparation produces only a local prompt receipt and no bundle or browser attachment.

The request file is the sole source for the frozen goal, question, notes, and `files`
array. Its digest is recomputed by the Executor before any bundle or browser mutation.
When supplied, `business_deadline` is a real business stop boundary, not a polling
interval or a parent-turn timeout. When it is omitted or null, no business deadline is
inferred: a wait/tool/mailbox/child-runtime limit only requests continuation of the
same attempt. The Executor must not invent a short observation deadline or report the
round failed because such a limit elapsed.

## Modes and ownership

### `prepare-and-run`

Use only with a v2 handoff after the parent has frozen the request and explicitly
authorized one Send. A v1 prepare-and-run handoff is rejected; v1 is retained only
for recovering an existing attempt.
The Executor validates the request and invokes the frozen context policy. For
`explicit`/`auto`, it builds and verifies the bundle, stages it through the existing
WSL/Windows adapter, and owns the browser-critical section from claim through upload
and preflight. For `none`, it does not stage or upload an attachment. Both paths send
at most once, promote a bootstrap URL when needed, then wait for the same submitted
turn and capture it through the existing native or browser fallback route; exact staged
file cleanup applies only when staging occurred.

An existing unresolved attempt, request drift, or a failed preflight blocks this mode.
The Executor does not create a replacement attempt to get around that state.

### `recover-only`

Use only for an existing attempt that has already crossed its saved boundary. The
Executor reads the attempt checkpoint first, rediscovering the same owner and exact URL
with a fresh page observation when browser access is needed. It may wait, promote a
promotion-ready bootstrap, or capture the already-pinned turn according to the
checkpoint. It must not package, stage, upload, fill, click Send, or create an attempt.

The same Agent and `attempt_id` are retained across a host/tool yield. `wait_agent`
timeout, a child runtime limit, or an unchanged page is not a business failure. A
continuation returns a non-terminal receipt and resumes with the same identity; it does
not authorize a new Agent, attempt, upload, or Send.

## Mechanical sequence

The Executor follows the existing helpers and references in this order:

1. Validate the v2 handoff shape, route identity, request digest, output boundary,
   explicit model/thinking controls, and external-action authorization. For an explicit
   target, verify the target Project before any bundle or browser mutation; do not
   create a temporary repository to avoid a binding mismatch.
2. In `prepare-and-run`, invoke the existing review/bundle preparation route with the
   frozen context policy. `none` passes `--repo-context none --max-files 0`, produces no
   bundle, and skips staging/upload because no browser attachment is authorized.
   `explicit`/`auto` verify the manifest, ZIP integrity, source digest, and (when
   authorized) Windows staging digest. For the Chrome DevTools route, create/check a
   `devtools-upload/v1` action plan whose only upload order is one attachment-control
   click, a fresh upload-menu snapshot, then direct `upload_file` on the
   **Upload from computer** UID; never click that menu item. Persist the normalized
   successful upload receipt beside the plan and pass both to browser preflight so
   the same plan, page, staged Windows path, digest, attachment name, and chip are
   checked.
   In `recover-only`, reuse
   the checkpoint and skip all preparation.
   The connector fallback may use `waitForEvent("filechooser")` plus its menu click only
   after a DevTools failure before any upload action/chooser; that sequence is never
   valid for DevTools.
3. Resolve the signed-in browser using the complete page list and requested owner
   observations. Use the canonical owner token plus exact URL; never guess from a title,
   selected tab, sidebar position, or “latest” item.
4. For a new conversation, retain the bootstrap owner until the exact post-Send URL is
   observed and promoted. For an existing conversation, verify its saved boundary.
5. For `explicit`/`auto`, verify the visible attachment, model/thinking observations,
   fresh page snapshot, and all preflight inputs through the existing gate. A DevTools
   bundle round is blocked unless its action plan and accepted upload receipt both pass
   the gate. Treat model/thinking controls as one bounded transaction: one combined
   initial read, only actions required by a mismatch, one combined final confirmation,
   and a validated `model-controls/v1` receipt passed to preflight. A missing, failed,
   unknown, stale, or over-budget receipt never authorizes Send. Do not recheck controls
   after preflight or during recovery. For `none`,
   verify the model/thinking observations, fresh page snapshot, and all non-attachment
   preflight inputs; do not invent an attachment check. If DevTools `upload_file` fails,
   is unknown, or the chip is absent, report possible native chooser residue, stop all
   upload/Send retries, and keep native-chooser cleanup separate from exact staged-file
   cleanup; return the plan's exact staged Windows path and SHA-256 for that separate
   cleanup, and never claim browser UI cleanup without same-host proof. A gate failure
   is terminal for this handoff and never a reason to resend.
6. Write the saved attempt checkpoint immediately before the one permitted Send. After
   visible acceptance, retain bootstrap identity until promotion succeeds, then release
   and clean only the exact staged file.
7. Wait for the pinned user/assistant turn. On completion, capture the full answer using
   the native reader or the visible Copy reply fallback, preserving the exact remote
   turn ID and capture metadata.
8. Save the immutable exchange and ledger records before returning success. The parent
   separately reopens the artifacts and records its Codex verdict.

The detailed command arguments, valid terminal states, browser observations, and
cleanup attestations are intentionally not repeated here. Use the canonical references
and existing scripts as the executable source of truth.

## Fail-closed and recovery rules

Return `blocked` or `failed` when any required input is missing, changed, ambiguous, or
outside the declared scope. In particular, stop on:

- request, manifest, source/staged bundle, answer, or artifact digest mismatch;
- conflicting owner tokens, page IDs, URLs, Project/account/workspace identities, or
  multiple plausible turns;
- a missing bound owner, stale page/snapshot, unresolved prior attempt, or unknown Send
  result;
- a server/account protection, login/2FA/CAPTCHA, remote-debugging permission, or
  unavailable browser/MCP capability;
- a response that is incomplete, errored, truncated, or not the pinned turn.

Never turn an observation timeout into “not sent,” “failed,” or permission to resend.
With no business deadline, preserve the checkpoint and return
`continuation-required` when a tool, mailbox, or child runtime limit ends the current
wait; the same Agent/attempt must resume. If an explicit business deadline is reached,
preserve the checkpoint and return the actual deadline diagnosis; the parent may later
resume the same attempt without creating a new one.
Only a real host automation ID, registered through `attempt_recovery.md`, permits a
claim of background waiting. Otherwise the parent must keep the same Executor handle
and wait again or resume it in `recover-only`.

A resolver HOLD or owner/URL mismatch is only an observation conflict. It does not prove
that a page returned to Project home and never authorizes navigation, reload, or opening
another page. Re-read the live URL and owner on the same pageId; report navigation only
when direct same-page evidence establishes it.

## Receipt format

The inter-agent response is a compact structured receipt, not a transport for the full
answer. It uses this shape (omit unavailable fields rather than inventing values):

```json
{
  "status": "complete",
  "mode": "prepare-and-run",
  "stage": "captured",
  "bridge_thread_id": "repo-date-task",
  "bridge_project_id": "local-project-id",
  "attempt_id": "attempt-id",
  "remote_turn_id": "remote-turn-id",
  "conversation_url": "https://chatgpt.com/c/exact-id",
  "bundle_path": "/absolute/path/bundle.zip",
  "bundle_sha256": "...",
  "staged_path": "/absolute/execution-host/staging/bundle.zip",
  "staged_sha256": "...",
  "upload_action_plan": "/absolute/path/for/bridge/artifacts/upload-action-plan.json",
  "upload_action_plan_verification": "verified",
  "upload_result": "/absolute/path/for/bridge/artifacts/upload-result.json",
  "upload_result_verification": "verified",
  "model_control_receipt": "/absolute/path/for/bridge/artifacts/model-controls.json",
  "model_control_receipt_verification": "verified",
  "answer_path": "/absolute/path/immutable-turn.md",
  "answer_sha256": "...",
  "capture_route": "native-read-thread",
  "selected_model": "observed-label",
  "selected_thinking_intensity": "observed-label",
  "context_policy": "explicit",
  "attachment_policy": "bundle",
  "expected_output_dir": "/absolute/path/for/bridge/artifacts",
  "next_action": "main-verification"
}
```

For a `none` round, `bundle_path`, `bundle_sha256`, `staged_path`, and
`staged_sha256`, `upload_action_plan`, `upload_action_plan_verification`,
`upload_result`, and `upload_result_verification` are omitted.
The receipt must still identify the same
`expected_output_dir`, attempt, conversation, captured answer, and
`context_policy = "none"`; absence of an attachment does not prevent the authorized
Send or subsequent capture.

`status` is one of `complete`, `continuation-required`, `blocked`, or `failed`.
`stage` identifies the last durable stage, such as `validated`, `staged`,
`send-started`, `send-accepted`, `waiting`, `captured`, or `diagnosed`. A successful
receipt requires an immutable answer path and digest; a continuation or blocker must
include the exact same attempt identity and a concrete `reason`/`next_action`.

Do not include the raw answer, a summary, a recommendation, or an unverified model
claim in the receipt. The parent reads `answer_path`, verifies its digest and content,
and owns every later scientific or engineering conclusion.
