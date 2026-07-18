# Bridge Project Protocol

Read this reference when creating, binding, routing, synchronizing, promoting,
or verifying Project-aware Codex Pro Bridge work.

## Identity

```text
one local root
  -> zero or one Bridge Project
  -> zero or one ChatGPT Project binding
```

The local root and remote Project ID are unique identities. A title is mutable
display metadata. Additional directories may be approved as task evidence, but
they do not become additional Project bindings.

## Execution shapes

```text
local_only
  Codex works directly

standalone
  Bridge Thread
    Codex Session
    standalone GPT Conversation

project
  Bridge Project
    Project Sources
    Bridge Thread
      Codex Session
      Project GPT Conversation
```

A Project may contain one or many Bridge Threads. A small Project can stay on
one Thread; Project mode does not require artificial task decomposition.

## Canonical state

Thread JSONL remains canonical for task rounds. Project activity JSONL is
canonical for binding, source synchronization, task membership, and task
status. Markdown indexes and overviews are derived views.

Old Thread events without `bridge_project_id` are standalone history. Promotion
attaches the Thread in Project activity. Later Thread events may carry the
Project ID; old events are never rewritten.

## Binding lifecycle

```text
no binding -> unverified -> active
                         -> stale
                         -> account_mismatch
                         -> missing
                         -> unbound

stale | account_mismatch | missing -> active after a correct visible verification
```

Only `active` is eligible for automatic Project routing.

`missing` means the previously bound Project could not be opened or observed.
It preserves the remote identity for recovery. `unbound` means the local
relationship was deliberately ended. Neither state deletes remote content.

Automatic Project routing also requires every recorded active Project Source to
match its local SHA and have `synced` status. No source manifest means no shared
source requirement; a stale, pending, failed, or missing recorded source blocks
submission until it is reconciled.

`read_only` prohibits source mutation. `append_only` permits new
bridge-managed resources and is the default. `managed` additionally permits
reconciliation of older bridge-managed resources. No mode permits automatic
deletion of user-managed content.

## Source synchronization

The local source file is canonical. Planning requires a complete browser-observed
remote inventory; an empty Project is represented explicitly as `[]`. The
remote name includes a content digest:

```json
{
  "files": [
    {"name": "paper.pdf", "ownership": "user_managed"},
    {"name": "bridge--brief-project-brief--abc123.md", "ownership": "bridge_managed"}
  ]
}
```

Do not label a file `bridge_managed` unless its prior name is already recorded
by the local source manifest.

```text
bridge--<role>-<name>--<sha12>.<ext>
```

In `managed` mode, replacement is two-phase:

1. Upload the new digest-named file.
2. Verify it is visible in the bound Project.
3. Remove the old bridge-managed version.
4. Record the observed final state.

If capacity has no temporary slot for this sequence, the plan is blocked.
Deleting a user file to make room is not a valid automatic plan.

In `append_only` mode, the new digest-named version may be uploaded but older
bridge-managed versions remain. Switching to `managed` is an explicit cleanup
decision.

## Task and conversation rule

The Bridge Thread is the Bridge Task identity. One Thread has one primary GPT
Conversation and may contain several review rounds in that conversation.
Independent deliverables use independent Threads and conversations.

When a standalone Thread is promoted, old events remain unchanged. An existing
standalone conversation can be reused only after it is visibly moved into the
bound ChatGPT Project. If that is impossible, retain it as historical evidence
and start a new Project task rather than silently rebinding the URL.

Task dependency edges point directly between Bridge Thread IDs. A Workstream
term is reserved for a future grouping that genuinely contains multiple tasks;
it is not part of the current protocol.
