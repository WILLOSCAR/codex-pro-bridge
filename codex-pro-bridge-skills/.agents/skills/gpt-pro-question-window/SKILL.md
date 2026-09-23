---
name: gpt-pro-question-window
description: Route a frozen Codex Pro question through the correct signed-in ChatGPT/GPT Pro conversation, using the specialized bridge_executor for bundle preparation, browser submission, unattended waiting, and raw capture; retain local verification in the main agent.
---

# GPT Pro Question Window

Use this skill only when outside reasoning is useful. It is the main-agent routing and
verification adapter for Codex Pro Bridge; it is not the executor's step-by-step
playbook.

## Host runtime

Invoke every helper with one explicit Python 3.10+ interpreter appropriate to
the host (`python` on Windows or `python3` on POSIX). Keep that interpreter
consistent throughout one Bridge round instead of relying on script shebangs.

## Canonical contracts

Before creating or resuming Bridge state, read
[bridge_protocol.md](references/bridge_protocol.md). Before an external browser round,
read [browser_adapters.md](references/browser_adapters.md) and
[attempt_recovery.md](references/attempt_recovery.md). When delegating the mechanical
round, read [executor_handoff.md](references/executor_handoff.md) completely and pass
its structured handoff to the global `bridge_executor` Agent.

The referenced documents and existing Python helpers are the sole owners of owner
tokens, exact URLs, page observations, attempts, preflight, browser actions, capture,
cleanup, and append-only ledger semantics. Do not copy those algorithms into this
skill or into a custom prompt.

## Main-agent responsibilities

当当前环境已配置并验收 `codex-pro-bridge` 执行 MCP 时，优先使用
[mcp_runtime.md](references/mcp_runtime.md) 的 `bridge_submit → bridge_wait → bridge_result`。
父代理仍使用下述确定性准备与最终核验；机械步骤由持久 worker 执行，不再分派 Executor。
运行时缺失或尚未验收时使用下面的 Agent 路线。任务一旦提交，沿用其 job/attempt，
不能因等待超时或阻塞切换另一条路线重发。

1. Define the exact question, desired output, and whether external reasoning is useful.
   Use `resolve_bridge_route.py` with `--external-reasoning` or `--local-only`. A
   `local_only` result ends this route without spawning an Executor.
2. Freeze the evidence decision and any explicit target Project URL. For a new round,
   run `scripts/prepare_bridge_execution.py` with the question file, notes, frozen
   context policy (`--context-policy explicit|auto|none`), model label/kind, thinking
   intensity, and `--allow-send`. `explicit` requires repeated `--file`; `none` must
   pass no `--file` and freezes `max_files = 0`; `auto` may use an empty focus list and
   freezes a positive `--max-files` limit. Contradictory policy/file combinations fail
   before any Project rebind.
   The command reuses the Project store/router owners, performs an explicit target
   rebind when requested, derives the canonical Thread ID, and atomically publishes
   request plus `executor_handoff/v2`. Do not hand-write JSON, guess a Thread ID, or
   create an isolated repository merely because the target differs from the current
   binding.
3. Use the receipt's handoff path and digest as the only Executor input. The request
   contains the frozen goal/question/notes/files; model controls and Project target
   live in v2 handoff. A business deadline may be supplied only when the parent/user
   explicitly defines one; otherwise omit it or set it to null.
4. Select exactly one execution mode and invoke the named `bridge_executor` Agent:
   `prepare-and-run` for the published v2 handoff with explicit one-Send authorization,
   or `recover-only` for an existing unresolved attempt. Do not split packaging and
   browser ownership across agents.
5. Keep the same Executor handle and use `wait_agent` for its completion, blocked,
   failed, or continuation receipt. A wait timeout, unchanged page, or child runtime
   limit is not task completion and does not authorize a new Agent, attempt, upload, or
   Send. Continue the same handoff in `recover-only` as directed by its receipt. A
   missing/null business deadline means there is no business termination time.
6. After a `complete` receipt, reopen the immutable answer, bundle, ledger, and
   checkpoint; verify hashes and the exact pinned turn. Record the separate Codex
   verdict with `record_codex_verdict.py`, then run the thread/Project verifiers before
   reporting conclusions. Never modify the raw Pro answer to add the verdict.

## Executor boundary

The Executor owns the mechanical round inside the frozen contract: request validation,
bundle creation, manifest/ZIP checks, WSL/Windows staging and digest verification when
an attachment is authorized, browser identity resolution, explicit target Project
verification and complete Sources inventory observation, scoped upload, fresh preflight,
one authorized Send, bootstrap promotion, exact-file cleanup, pinned-turn waiting, raw
answer persistence, and release. On the Chrome DevTools route, the upload action is
strictly one click on Add files, a fresh same-page snapshot, then direct `upload_file`
on the fresh Upload from computer UID; never click that menu item or click Add files
again after a missing chip. Use `scripts/validate_devtools_upload.py` to check the
structured action plan, persist it under the handoff's expected output directory,
normalize and persist its successful upload receipt, and pass both to browser preflight;
a missing, failed, unknown, or stale receipt blocks before Send. Stop on its native chooser risk result. For
`context_policy = none`, it passes
`--repo-context none --max-files 0`, does not stage or upload a nonexistent/unauthorized
attachment, does not create a bundle, and still may perform the authorized Send and
capture. It must use the existing helpers and fail closed on identity, digest,
authorization, or turn ambiguity.

For browser model controls, require one `model-controls/v1` transaction: one combined
initial read, only mismatch-driven adjustments, and one combined final confirmation.
Pass its receipt to preflight and never recheck controls after preflight or during
recovery. A recovery owner/URL HOLD is an observation conflict, not proof of navigation;
the Executor must not navigate or reload to repair it and may describe a transition only
from direct same-page URL observations.

The connector fallback's `waitForEvent("filechooser")` plus menu-item click is a
different route and must only be used after a qualifying pre-Send DevTools failure
before any upload action/chooser; never mix it into the DevTools upload sequence.

The main agent retains the purpose, evidence scope, question text, Project decision,
external-action authorization, interpretation of any error, scientific/engineering
verification, and final verdict. The Executor must not choose materials, rewrite the
question, adopt or summarize the answer, or put long answer text in its inter-agent
receipt; it returns paths, hashes, identities, status, and concrete blockers only.

## Completion and failure semantics

An external round is complete only when the raw exchange is saved as an immutable turn
artifact on the intended Bridge Thread and the receipt includes the exact answer path,
digest, remote turn ID, URL, capture route, and observed model metadata. A successful
Send alone is not a completed review. A host wait window or runtime yield is not a web
failure; preserve the attempt and continue with the same identity. A declared business
deadline is handled as an actual deadline, not relabeled as a send failure. Unknown Send
outcomes always remain non-resendable.

Stop and report a concrete blocker for login, 2FA, CAPTCHA, remote-debugging permission,
account-security prompts, rate limits, unavailable host/MCP capability, owner/URL/
Project ambiguity, hash drift, stale observations, missing target turn, or incomplete
or truncated answers. Do not switch to a new conversation or silently weaken a gate.

## Normal question

For ordinary questions, read [question_window_prompt.md](references/question_window_prompt.md)
and place the final prompt in the frozen request's `question` field. Specialized review
skills provide their own prompt; they still use this skill for route selection, the
Executor handoff, capture, and final verification.

## Scope boundary

Task Bundles are immutable, round-scoped evidence and are not Project Sources. Keep
Project Source changes in `gpt-pro-project-workspace`. Keep bundle selection logic in
`bundle-algorithm-context`; this skill only consumes the already-frozen request. Keep
the full raw exchange and local verdict separate. Use the Chrome DevTools route and the
existing browser adapter's fallback only as its canonical contract permits; never open,
close, or repurpose a browser tab outside that contract.
