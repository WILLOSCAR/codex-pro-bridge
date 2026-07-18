---
name: gpt-pro-project-workspace
description: Bind one local research repository to one existing or new ChatGPT Project, synchronize stable Project Sources, inspect Project tasks, or repair Project routing. Use when the user mentions a ChatGPT Project, shared files across conversations, several research sessions, project binding, or Project-aware Codex Pro Bridge work; normal external questions still enter through gpt-pro-question-window, which resolves the route automatically.
---

# GPT Pro Project Workspace

Manage the optional Project layer above existing Bridge Threads. Read
[references/project_protocol.md](references/project_protocol.md) before any
binding, source synchronization, promotion, or repair.

## Invariants

- One local repository has at most one Bridge Project.
- One Bridge Project has at most one current ChatGPT Project binding.
- Never bind or verify by title alone. Record the visible Project ID/URL and the
  observed account/workspace.
- Existing remote files, instructions, and conversations are user-managed.
  Never delete or replace them automatically.
- A Bridge Task is the existing Bridge Thread. Do not create a parallel
  Workstream identity for the same deliverable.
- Project Sources are stable shared context. Task Bundles remain scoped to one
  thread and one review round.

## Automatic route

Before an external round, Codex decides whether external reasoning is useful and
runs `scripts/resolve_bridge_route.py` in preview mode. Codex passes
`--external-reasoning` when a GPT Pro round is useful or `--local-only` when it
will complete the task itself. This is an agent decision; the user does not
choose a route manually.

Use `local_only` for work that Codex should complete directly. Use `standalone`
for a one-off external review without a Project. Use `project` when the current
repository has one active binding or the user explicitly requests shared
Project context.

Only rerun with `--apply` after the decision has no
`requires_confirmation` entries. Report the chosen scope, Project, Thread,
conversation policy, and reason in one concise line.

If routing reports `sync-project-sources`, do not submit against stale shared
context. Reconcile the selected Project Sources and preview the route again.

## Bind an existing Project

1. Create the local Project identity with
   `scripts/manage_bridge_project.py create` if it does not exist.
2. Open the user's signed-in Chrome profile and navigate to the exact existing
   ChatGPT Project.
3. Read the visible Project URL/ID, title, account, and workspace. Do not infer
   them from a similarly named sidebar item.
4. Record the relationship with `scripts/manage_bridge_project.py bind`.
5. Re-open the saved URL and record the second observation with
   `scripts/manage_bridge_project.py verify-binding`.
6. Run `scripts/verify_bridge_project.py --require-active-binding`.

Binding is a local relationship. Unbinding must not delete the ChatGPT Project,
its conversations, its instructions, or its files.

If the saved Project can no longer be opened, record that observation with
`scripts/manage_bridge_project.py mark-binding-missing`. Do not silently unbind
or choose another similarly named Project. A later successful
`verify-binding` restores the same binding to `active`.

## Project and task lifecycle

Use `scripts/manage_bridge_project.py update` to change the local title or brief
without rebinding. Use `update-task` for task title, goal, or dependencies and
`set-task-status` for workflow state. Dependency edits are rejected when they
refer to an unknown task or introduce a cycle.

Use `archive` to stop routing new work through a local Bridge Project while
preserving all local and remote history. Use `reactivate` before resuming it.
Archiving, reactivation, task edits, binding loss, and recovery are recorded in
Project activity; never edit the JSONL ledger by hand.

## Project Sources

1. Inventory the visible Project Sources before planning.
2. Save that complete observation as JSON, including `[]` for an empty
   Project, and run `scripts/plan_project_source_sync.py` with
   `--remote-inventory-file` plus the selected stable local files.
3. Stop if the plan is blocked, names a sensitive file, or exceeds capacity.
4. Upload every `uploads[].upload_path` through the visible Project source
   control. Each staged basename already equals the planned digest-bearing
   `remote_name`; do not upload the original local path under a different name.
5. In `managed` mode, verify every uploaded filename before removing an older
   bridge-managed version. In the default `append_only` mode, retain old
   versions.
6. Never remove a user-managed filename.
7. Record only observed effects with
   `scripts/record_project_source_sync.py`.
8. Run `scripts/verify_bridge_project.py --require-active-binding
   --require-synced-sources --require-inventory-verified`.

`--assume-plan-complete` is allowed only after every desired source and planned
bridge-managed removal has been visibly checked. It still requires a complete
post-operation `--remote-inventory-file`; the bridge never manufactures its own
success observation.

Before a later Project round, inventory the visible sources and run
`scripts/reconcile_project_source_inventory.py`. A missing or misowned
bridge-managed source is recorded locally and blocks routing until the visible
inventory recovers.

## Conversation handling

Existing Project conversations remain external references until the user adopts
one for a Bridge Task. New independent deliverables receive new Bridge Threads
and new Project conversations. Follow-up rounds for the same deliverable reuse
the bound conversation.

When promoting a standalone Thread, attach it with
`scripts/manage_bridge_project.py attach-task`. Do not rewrite old Thread
events. A route with conversation policy `verify-or-rehome` means the saved
standalone conversation must be visibly moved into the Project before reuse.
Verify the destination and keep the same conversation URL. If it cannot be
moved, preserve it as history and create a new Bridge Task and Project
conversation.

## Browser seam

Use the Chrome connector and visible semantic controls. Project discovery,
source inventory, upload, conversation creation, and instruction editing are
browser adapter operations; none of them changes local identity until an
observation is recorded by a script.

Do not store cookies, tokens, private web responses, or account credentials in
Bridge state. Stop for login, 2FA, CAPTCHA, service protections, account
mismatch, missing Project access, or UI state that cannot be verified.
