---
name: gpt-pro-review-probe
description: Fast lane for high-frequency, fine-grained, parallel GPT Pro review. Send one mature idea, research proposal, or atomic sub-task from one research line to GPT Pro as a standalone, single-round Review Probe that never touches the Bridge Project source-sync gate, so unrelated stale shared sources never block it and many probes run in parallel. Use when reviewing per-idea/per-proposal/per-task across several research lines at once; use gpt-pro-question-window or gpt-pro-project-workspace when a round genuinely needs shared durable Project context.
---

# GPT Pro Review Probe

## Host runtime

Invoke every helper with one explicit Python 3.10+ interpreter appropriate to
the host (`python` on Windows or `python3` on POSIX). Keep that interpreter
consistent throughout one Bridge round instead of relying on script shebangs.

Use this skill for the high-frequency review loop: repeatedly send a specific
idea, proposal, or atomic sub-task to GPT Pro as an adversarial reviewer, across
several research lines running in parallel.

A **Review Probe** is a `standalone`, single-round Bridge Thread. It never
attaches to a Bridge Project, so it never consults Project source-sync and is
**never blocked when unrelated shared Project sources are stale or missing**.
Each probe is its own thread, conversation, ledger, durable tab owner token, and
host-local conversation claim, so probes may use different dedicated tabs in
parallel. The same conversation remains exclusive.

This skill builds on [gpt-pro-question-window](../gpt-pro-question-window/SKILL.md)
for the browser and persistence seam. Read
[its bridge protocol](../gpt-pro-question-window/references/bridge_protocol.md)
for IDs, events, and invariants.

## When to use it

- Reviewing one mature idea, proposal, or atomic sub-task from one line.
- Running several such reviews across research lines at the same time.
- Any round where GPT Pro only needs the scoped bundle, not shared Project context.

Do NOT use it when the round genuinely needs shared durable Project sources
(a project brief, glossary, standing PRD). Use `gpt-pro-project-workspace` then.
Never let a probe author research judgment: its result is external pressure only,
recorded in the owning research system, never a decision.

## Probe identity

```text
bridge_thread_id = <track>-<candidate>-<probe-slug>-<yyyymmdd>
  e.g.  m-ai-c14-reward-leakage-20260809
```

`track` and `candidate` are opaque passthrough labels; Bridge never parses them.
One probe = one thread = one `codex-snapshot → gpt-exchange → codex-verdict`
round = disposable.

## Required flow

1. Open the probe — writes the immutable Codex snapshot and the standalone bundle:

   ```bash
   python3 \
     ${CODEX_HOME:-$HOME/.codex}/skills/gpt-pro-review-probe/scripts/open_review_probe.py \
     --repo . \
     --track m-ai --candidate c14 --probe-slug reward-leakage \
     --goal "<what GPT Pro should review>" \
     --question "<focused question>" \
     --summary-file /tmp/probe-notes.md \
     --mode algorithm_review --repo-context auto
   ```

   It prints `probe_thread_id`, `codex_notes`, and `bundle`. It never runs
   source-sync and never asks for Project confirmation.

2. Use the DevTools-first route in the Question Window's
   [browser adapter reference](../gpt-pro-question-window/references/browser_adapters.md).
   Reuse a conversation claim when a reserved chat ID exists; otherwise acquire
   a standalone bootstrap claim before touching the ChatGPT home tab:

   ```bash
   python3 \
     ${CODEX_HOME:-$HOME/.codex}/skills/gpt-pro-question-window/scripts/manage_browser_lease.py \
     --repo . acquire --holder <worker-id> \
     --bridge-thread-id <probe_thread_id> \
     --scope profile --bootstrap
   ```

   Chrome DevTools MCP is the primary route. The connector is permitted only
   for the documented pre-submit fallback. Pass the complete `list_pages` result
   to `resolve-tab`; when requested, read `sessionStorage` owners only on those
   pageIds and resolve again. Open exactly the returned canonical URL only for
   `open-canonical-tab`, and after an owner write rerun resolution to confirm
   `tab_bound`. Never recover with an unapproved `new_page`, `close_page`,
   `localStorage`, unknown URL query parameters, or fragments. A bootstrap holds profile
   scope only until the first Send is promoted to its new conversation.

   页面观察与参数格式复用 Question Window adapter；已有唯一 owner 不因额外未占用重复页而换页。
   Project 首页重复页自动选择的规则不用于绕过 standalone 首页的身份范围。

3. Stage the bundle with `manage_browser_staging.py`, upload only its returned
   G-drive Windows path through DevTools MCP and a visible semantic control, and
   record the actual route. Gate with `check_browser_preflight.py`, including
   source/staged paths, host/temp root, exact URL, complete `pages-json`, the
   resolver-produced match count, pageId/fresh snapshot pageId, owner token,
   checked model item and selection kind, complete visible thinking-strength value, attachment, conversation, existing turn boundary, and
   exact prompt SHA-256. Bootstrap uses `--conversation-bootstrap` and the
   `new-conversation` boundary. These last two observations must be recorded before
   running the gate or Send.

4. 先按 [发送检查点与等待恢复](../gpt-pro-question-window/references/attempt_recovery.md)
   保存预检结果和问题，创建 attempt，在点击前写入 `send-started`。Send once. For bootstrap, keep the claim until the same owned page exposes an
   exact conversation URL, then call `promote-bootstrap`. After visible acceptance
   and any required promotion, record the submission time and target turn ID,
   then release the claim with staged WSL path, SHA-256, and
   `--terminal-state send-accepted`; this precisely deletes that staged file.

5. 仅在本轮工具实际支持 ChatGPT 对话读取时使用原生 `read_thread`，否则走浏览器读取。
   Match the new user turn after the saved boundary, pin its remote turn ID, and
   accept only its `completed`, untruncated assistant reply. Then capture with
   `save_bridge_turn.py --standalone --single-round --capture-route
   native-read-thread --remote-turn-id <turn-id> --attempt-id <attempt-id>`. Do not pass the released
   browser token. Record the Codex verdict separately.

6. If the native result is unavailable, ambiguous, or `truncated`, reacquire
   the same conversation claim, require exactly one tab matching owner token
   plus exact URL, obtain a fresh pageId/snapshot, prove the exact user turn lies after the
   saved boundary and matches the prompt SHA-256, then capture the pinned turn
   with those observations, the same remote turn ID, and `browser-fallback`. Never use the
   latest response by position. Release in every terminal path.

7. 用 attempt helper 的 `wait-plan` 根据当前工具选择等待方式；没有宿主唤醒能力时保持
   活跃线程轮询。真实 watcher 登记、停止及中断恢复统一遵循上述参考，不能重复创建或重发。

`open_review_probe.py` prints a handoff outline for steps 2–7 with the resolved
thread id and bundle path; scheduled-task creation and browser actions remain
host-tool operations rather than shell commands.

## Parallel operation

- **Runs in parallel across lines:** `open_review_probe.py` (snapshot + bundle),
  and the local Codex verdict. Each probe is a distinct thread with its own lock
  and ledger, so concurrent probes never fork or block each other.
- **Scoped browser concurrency:** different conversations may mutate their own
  owned tabs concurrently. A second owner for the same conversation and a
  Project mutation conflicting with its conversations fail closed. Claims are
  released while remote generations run.
- **SSH boundary:** remote repository or compute work may run in parallel, but
  the browser-host dispatcher owns staging, upload, preflight, and Send. A
  remote Codex process does not inherit access to the operator's local Chrome.
- **Contamination guard:** the conversation-id gate makes "right Project, wrong
  chat" fail closed, so a probe's result can never be recorded against the wrong
  chat. Record `observed_conversation_id` on each turn for later reconciliation.

## Non-negotiable checks

- A probe is standalone: it must not attach to a Bridge Project. `--standalone`
  fails closed if the thread is attached.
- One round per probe: `--single-round` refuses a second exchange on a thread；同一 attempt 的同一答案允许幂等恢复。
- Verify locally before trusting a result; keep GPT Pro output as pressure only.
- Never send on a tab whose owner token, exact URL, pageId/fresh snapshot, or
  conversation ID fails the gate.
- Never capture "the latest turn" by position alone; bind the response to the
  post-submit remote turn ID. A truncated native item is not a raw answer.
