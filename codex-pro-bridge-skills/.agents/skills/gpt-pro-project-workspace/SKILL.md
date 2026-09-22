---
name: gpt-pro-project-workspace
description: Bind one local research repository to one existing or new ChatGPT Project, synchronize stable Project Sources, inspect Project tasks, or repair Project routing. Use when the user mentions a ChatGPT Project, shared files across conversations, several research sessions, project binding, or Project-aware Codex Pro Bridge work; normal external questions still enter through gpt-pro-question-window, which resolves the route automatically.
---

# GPT Pro Project Workspace

## Host runtime

Invoke every helper with one explicit Python 3.10+ interpreter appropriate to
the host (`python` on Windows or `python3` on POSIX). Keep that interpreter
consistent throughout one Bridge round instead of relying on script shebangs.

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

Before an external round, Codex decides whether external reasoning is useful. For a
new Bridge Executor round, `gpt-pro-question-window/scripts/prepare_bridge_execution.py`
is the parent-side entry point: it calls the route/store owners, freezes the context
policy and materials, and publishes the canonical Thread ID plus handoff. Use
`--context-policy none` with no evidence files for a reasoning-only round; it retains
Send/capture authorization but does not authorize an attachment upload. Do not manually
compose the request or call `resolve_bridge_route.py` only to manufacture a second
Thread ID.
Use `resolve_bridge_route.py` directly for a side-effect-free route preview or a
local-only decision.

Use `local_only` for work that Codex should complete directly. Use `standalone`
for a one-off external review without a Project. Use `project` when the current
repository has one active binding or the user explicitly requests shared
Project context.

The preparation entry point accepts an explicit target Project URL. That URL is the
current call's authorization: it safely rebinds this repository through the existing
store owner, archives the old local Sources manifest, and leaves the new inventory
unverified until the Executor observes it. It must not create a temporary repository
or block merely because the target differs from the previous binding. Without an
explicit target, reuse the current binding and retain the existing stale/ambiguous
gates. Report the chosen scope, Project, Thread, conversation policy, and reason in
one concise line.

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

用户明确切换到另一个 Project 时，优先由
`prepare_bridge_execution.py --target-project-url ...` 调用现有 rebind owner；
底层 `manage_bridge_project.py bind ... --rebind` 仍是修复/维护入口。两者都会原样
归档旧 Sources manifest，并为新 Project 创建 `inventory_state=unverified` 的独立清单；
其中空 `sources` 不表示远端没有文件。观察新 Project 的完整 inventory 后运行
`reconcile_project_source_inventory.py`，核验成功才继续该项目的咨询。
若在清单与绑定两次写入之间中断，用相同目标重试 `--rebind`；不要手工移动清单或放宽身份检查。
同一 Project 的重新核验保留原清单，既有网页文件和对话不迁移、不删除。

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

Source sync is **explicit by default**. `plan_project_source_sync.py` freezes
every already-synced source at its confirmed remote version unless you name it
with `--only`/`--source`. A local edit to an unlisted source never triggers an
upload or replacement, so routine local churn never overwrites remote sources or
blocks routing. Use `--all` to deliberately re-scan and push every changed
source (the pre-freeze behavior).

1. Inventory the visible Project Sources before planning.
2. Save that complete observation as JSON, including `[]` for an empty
   Project, and run `scripts/plan_project_source_sync.py` with
   `--remote-inventory-file`. Add `--only <path>` for each source you want to
   push this round; omit it to freeze everything, or pass `--all` to re-scan
   all known sources.
3. Stop if the plan is blocked, names a sensitive file, or exceeds capacity.
4. Upload every `uploads[].upload_path` through the visible Project source
   control. Each staged basename already equals the planned digest-bearing
   `remote_name`; do not upload the original local path under a different name.
   Frozen sources appear under `frozen_sources`/`reuse`; they are never uploaded
   or removed.
5. In `managed` mode, verify every uploaded filename before removing an older
   bridge-managed version. In the default `append_only` mode, retain old
   versions.
6. Never remove a user-managed filename.
7. Record only observed effects with
   `scripts/record_project_source_sync.py`. Frozen sources keep their prior
   status from the observed inventory; they do not regress to pending.
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

Use Chrome DevTools MCP as the primary route using
[`browser_adapters.md`](../gpt-pro-question-window/references/browser_adapters.md)
and use visible semantic controls. Use the Codex Chrome connector only for the
documented pre-submit compatibility fallback. Acquire a host-local
`--scope project` claim before Project source upload/removal, conversation
creation, instruction editing, or another shared Project mutation; it conflicts
with active conversation claims in that Project. Normal task rounds use their
own conversation claims and dedicated tab owner tokens, so different
conversations may proceed in parallel. None of these browser effects changes
local identity until a script records an observation. Stage every upload through
the digest-verified G-drive browser-host adapter; SSH access does not make a
remote absolute path uploadable by Windows Chrome.

Do not store cookies, tokens, private web responses, or account credentials in
Bridge state. Stop for login, 2FA, CAPTCHA, service protections, account
mismatch, missing Project access, or UI state that cannot be verified.
