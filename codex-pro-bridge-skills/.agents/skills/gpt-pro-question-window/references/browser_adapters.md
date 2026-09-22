# Browser Adapters

Read this reference whenever a Bridge round uploads a Task Bundle, mutates a ChatGPT page, performs browser fallback capture, or combines local browser control with SSH execution.

## Use the DevTools-first route

Use this order for every browser-mutating critical section:

1. Use the `chrome-devtools` MCP tools as the primary browser route. Prove that the tools are callable and connected to the intended signed-in Chrome profile; configuration alone is not connection proof.
2. Use the Codex Chrome connector only as a compatibility fallback when DevTools MCP is unavailable or fails before any DevTools upload action/chooser could start and the composer remains empty. Never switch after `upload_file` has been invoked or its chooser/chip outcome is unknown.
3. Use Computer Use only for a native or graphical boundary that neither browser route can control.

Keep one route active from destination verification through upload, preflight, and Send. Switch to the connector at most once, only before Send, after proving that no attachment, upload, or prompt was submitted. Do not probe both routes merely to compare speed.

Completion criterion: record the selected route, browser host/profile, visible attachment, and selected model. Existing chats require an exact conversation ID before Send; a new-chat bootstrap requires an exact Project/home destination before Send and an exact conversation URL immediately after the first accepted Send.

## Resolve the host topology

Keep these hosts distinct:

- **execution host:** repository, tests, jobs, or GPU work;
- **browser host:** the signed-in Chrome profile displaying ChatGPT;
- **MCP host:** the process running `chrome-devtools-mcp`.

The default supported topology requires the MCP host and browser host to be the same machine. The execution host may be different:

| Topology | Bridge behavior |
| --- | --- |
| WSL Codex + Windows MCP/Chrome + local repository | Load the `wsl-windows` host config, stage through its execution root, and upload only the returned `staged_windows_path` after SHA-256 verification. |
| WSL Codex + Windows MCP/Chrome + repository or compute reached through SSH | Fetch only the approved bundle to WSL, verify its digest, then use the same configured browser-host staging adapter. |
| Remote Codex + remote MCP/Chrome on the same graphical host | Use only after the remote Chrome profile, remote-debugging permission, and upload path are visibly verified. |
| Remote Codex + local Chrome on another machine | Treat as unsupported by default. `--autoConnect` searches the MCP host, not the operator's Mac. Prefer a local Bridge dispatcher. An explicitly authorized SSH tunnel plus `--browser-url` may support inspection, but do not submit until the debugging endpoint is loopback-only and the bundle path is proven readable by the browser host. |

SSH access to an execution host is not browser access. Never expose a Chrome debugging port on a public or shared interface. Browser ownership is host-local and keyed by profile plus exact Project/conversation: different conversations may proceed concurrently, while the same conversation and shared Project mutations conflict.

## Chrome DevTools MCP

Treat MCP configuration as discoverability, not connection proof. Acquire a conversation claim for an existing chat, or a one-time Project/profile bootstrap claim for a new chat, then:

1. Let the user approve Chrome's remote-debugging prompt when required. One multi-step validation sequence must reuse the same MCP process and owned tab; restarting MCP between assertions repeatedly triggers this permission and invalidates page/snapshot IDs. Stop for login, account security, CAPTCHA, or unexpected profile selection.
2. Call `list_pages` and pass the complete result, including non-ChatGPT pages, to `manage_browser_lease.py resolve-tab`; the model does not prefilter pages or calculate matching counts. Only `open-canonical-tab` permits `new_page`, using exactly its `canonical_url`. Existing conversation claims never open Project/home or another tab. 唯一匹配 owner 优先；没有 owner 的重复 Project 首页自动选择可用且 pageId 最小的页面，其他页不操作，不要求用户关闭。已绑定 owner 缺失、同一 owner 多页或 owner/身份不符仍 HOLD。Never call `close_page`.
3. If resolution returns `read-owners-on`, read `sessionStorage['codex-pro-bridge.tab-owner.v1']` only on those current-process pageIds and rerun with those observations. Follow only the returned action. After `set-owner-token`, `replace-stale-owner-token`, or `replace-legacy-owner-token`, read the value back and rerun resolution; `tab_bound` becomes true only after the claim owner is actually observed. The legacy action only consolidates an exact conversation page from an older aliased-profile token to that conversation's newest durable token; two observed compatible owners HOLD as a duplicate. A bootstrap owner on a same-Project `/c/<id>` is `promote-ready`; any other owner/URL mismatch HOLDs. Never use `localStorage`.
4. Stage the Task Bundle with `manage_browser_staging.py`; compare source and staged SHA-256 and use only its returned browser-host path (`staged_browser_path`, also exposed as the compatibility field `staged_windows_path`).
5. Take a fresh snapshot of that page and locate ChatGPT's visible **Add files** (or equivalent attachment) control. Click that attachment control exactly once to open the menu. Do not click the menu item that says **Upload from computer** on the DevTools route.
6. Immediately take a fresh snapshot on the same pageId, locate the **Upload from computer** menu item's UID, and call DevTools `upload_file` directly with that UID and the verified browser-host path. Freeze the structured `devtools-upload/v1` action plan and normalize the MCP result with `scripts/validate_devtools_upload.py`, persisting the successful receipt beside the plan. Its only accepted order is `click attachment-control` → `fresh-snapshot upload-menu` → `upload_file from-computer-menu-item`.
7. Require the exact staged filename and a visible attachment chip. If `upload_file` fails, returns an unknown/error result, or the chip does not appear, stop all upload and Send actions: a native Windows file chooser may still be open. Do not click the attachment control again, do not click the menu item, and do not claim browser UI/chooser cleanup without an explicit same-host observation. The structured guard returns the exact staged browser path and SHA-256 for separate cleanup. Clean that exact staged file separately; this is not proof that a native chooser or page attachment was cleared.

The guard validates the recorded action plan/result but cannot intercept an MCP call or
observe an OS-native dialog. The Executor must therefore use the same MCP process,
pageId, and fresh snapshot that the plan records; an unknown native chooser state stays
blocked and is never converted into a retry.
8. Verify both independent inference controls as one bounded transaction. Collect one combined initial read of model and thinking labels. If the model already matches, never open its menu; otherwise open/select at most once. If thinking already matches, do not touch it; otherwise open it once, use bounded real steps, and read only thinking progress after each step. Take one combined final confirmation and persist a validated `model-controls/v1` receipt in the preparation directory. Do not perform a third full-pair confirmation, and never recheck either control after preflight or during recovery. A named item such as `GPT-5.6 Sol` uses `--model-selection-kind exact`; a dynamic item such as `最新` uses `--model-selection-kind latest-alias` and proves only that the alias was selected, not that it resolves to Astra. Read thinking strength using its complete visible value, including its numeric level when shown (for example `6 Pro`). Neither the strength value nor an account label containing `Pro` proves the model.
9. Pass all host, staging, URL, complete `--pages-json` and `--owners-json`, resolver-produced match count, pageId/snapshot, owner-token, requested/checked model, requested/selected thinking intensity, attachment, pre-submit boundary, and prompt-SHA observations to `check_browser_preflight.py`. For DevTools, also pass the repo-local `--upload-action-plan`, its successful `--upload-result` receipt, and the `--model-control-receipt`; preflight checks that they bind the same page, owner, URL, staged Windows path, digest, attachment name, visible chip, requested model, and thinking intensity. A missing, unknown, failed, stale, or over-budget receipt blocks before Send. Existing chats pass both conversation IDs. Bootstrap passes `--conversation-bootstrap`, no conversation IDs, and the exact boundary `new-conversation`; the gate recomputes the count and verifies the unique selected owner.
10. After the first bootstrap Send is visibly accepted, keep the claim live, wait for that same owned page to expose one exact conversation URL, take a fresh same-page snapshot, and call `promote-bootstrap`. Only then release with `send-accepted` and exact staged-file cleanup. If the URL is missing or ambiguous, do not release or resend; preserve the live claim for recovery.
11. For a dry run or proven pre-Send failure, remove the attachment and verify an empty composer. If this bootstrap claim owns the tab, clear only its exact `sessionStorage` owner token and verify the key is empty before release; attest `owner-token-cleared`. If no prepare action was applied, attest `owner-token-never-bound`. No promotion is needed because nothing was sent. A possible native chooser still needs explicit same-host cleanup/observation; never use a global Windows dialog killer.

Do not enable unrestricted filesystem paths to fix a missing root. Configure one narrow execution-host root and its corresponding browser-visible Windows root through `CODEX_PRO_BRIDGE_HOST_CONFIG`; the examples under `config/` show both supported topologies. The adapter does not bypass ChatGPT file-size, type, quota, model, service, or account restrictions.

The MCP may see every open window in the connected Chrome profile. Page visibility is not ownership: every mutation must pass the durable owner-token and exact-conversation gates. Separate owned conversations can operate in parallel.

### 页面观察和释放参数

`browser_observations.py` 统一转换当前支持的输入形状，resolve、preflight 和 recovery 共用该入口：

- `--pages-json` 可直接传完整 `list_pages` MCP 返回对象，也接受页面数组；页面 ID 字段支持
  `page_id`、`pageId`、`id`。别名同时存在但值冲突时失败，不丢弃非 ChatGPT 页来凑数量。
- `--owners-json` 为逐页数组，可用 `page_id`＋`owner_token`；也可保留原结果，形如
  `{"pageId": 1, "observation": <该页 evaluate_script 的完整 MCP 返回对象>}`。
  读取脚本应返回实际 `url` 和 `owner`；owner 字段支持 `owner_token`、`tab_owner_token`、`owner`，
  空值代表未占用，缺失字段不能自行补成空值。MCP 错误或无法确定的数据形状均明确失败。
- 在请求的全部 ChatGPT 页上读 owner。预检和恢复传同一组完整页及 owner 观察；只有完整列表里
  恰好一个 ChatGPT 页时，显式观察到的 owner 已是全集，可省略 `--owners-json`。
- `matching_page_count` 是匹配 URL 的候选页总数；`owner_selected_count=1` 表示已选定唯一当前
  owner。按解析器返回的 `page_id`、`url`、`tab_owner_token` 和候选总数传参，不把总数手改为 1。
- Project 首页只额外接受原样 `?tab=chats`，不删除或忽略任意查询参数；没有 query 的规范 URL
  仍用于新建页。绑定和预检保留实际观察到的 URL。

acquire/resolve 的 `release_argv` 是可追加到同一脚本及 `--repo` 后的 argv 数组，
只描述无暂存文件的 claim 释放，不推断发送结果；不能将 JSON 数组当成 shell 字符串执行。
有暂存文件时仍提供精确路径、SHA-256 和实际终态；提升后的返回参数为 `send-accepted`。
纯读取或捕获后可使用 `release --token <token> --terminal-state captured`，它只接受已提升的
conversation claim 且不执行暂存清理。未提升的 bootstrap 仍需原有 owner 清理证明，不能用
`captured` 或 `send-accepted` 绕过提升。无暂存文件不再禁止终态参数。

## Preserve reply structure

`save_bridge_turn.py --capture-route browser-fallback` 的页面身份使用同一套完整
`--pages-json` / `--owners-json` 观察。候选 URL 超过一个时必须提供这些证据，由解析器确认
唯一 owner 后捕获；不能把 `--matching-page-count` 人工改成 1 来绕过重复页。

For browser fallback, do not persist `innerText` as a lossless answer. It flattens
KaTeX, headings, tables, code spans, lists, and source links. On the pinned,
completed assistant turn:

1. Read compact DOM counts for `[role=math]` split by inline/block layout plus
   headings, tables, `pre` blocks, and links or source-reference buttons.
2. Use a fresh snapshot and click that turn's visible **Copy reply** control.
3. Immediately run `scripts/capture_copied_reply.py --output <fresh.md>` with
   the observed `--expected-*` counts. This Windows-browser adapter reads the
   clipboard through `powershell.exe` and writes Markdown directly to WSL
   without routing the full answer through model context.
4. Save that file with `save_bridge_turn.py`, passing `--answer-file <fresh.md>`
   and `--answer-format copied-markdown`.

Keep `innerText` only for completion, first/last-text, and truncation checks. If
Copy reply is unavailable, record `plain-text-degraded`; do not describe it as
the full raw Markdown answer. Do not save the full page HTML merely to preserve
formatting.

## Bounded fast path

Once the DevTools tools are callable in the current MCP process, do not reconnect
or repeatedly rediscover their schemas. Use one `list_pages` observation for the
identity phase, read requested owners concurrently when the tool runner permits,
and rerun `resolve-tab` once with those results. After resolution, one read-only
`evaluate_script` may collect `location.href`, the session owner, composer
emptiness, and visible model/thinking button text together. It never clicks,
changes attributes, or substitutes for the fresh snapshots required to locate a
semantic upload/Send control and verify an attachment.

If the complete visible thinking label already equals the requested value, do
not open its menu. Otherwise use the visible semantic control, move through its
reported range one real keyboard step at a time with a bounded maximum, and
re-read the complete label after each step. Stop on no progress or an unexpected
range; never use DOM attribute changes as evidence that React accepted a value.

按 [attempt_recovery.md](attempt_recovery.md) 创建检查点，紧接 Send 前保存 `send-started`；
恢复时检查点优先于“输入框为空”的推断。Fill the prompt and click Send once. An empty composer plus the visible stop-
generation control is evidence for `submitted` and `generation observed`, so
record and report that state immediately instead of sleeping in the browser
critical section. For an existing conversation, release after this visible
acceptance. For bootstrap, first resolve the same owned page, promote its exact
new `/c/<id>`, then release. Response completion remains a later native-read or
watcher event and must not be reported as complete at submission time. 等待方式由实际工具能力和
`wait-plan` 决定；没有宿主唤醒时不承诺后台回收。

For a pinned submitted turn, the passive `wait-script` recipe in
[attempt_recovery.md](attempt_recovery.md#活动工具等待配方) reuses the resolved page
and checks its owner and exact URL within each read. Release the short resolution
claim before this read-only wait; reacquire it and a fresh snapshot for Copy reply.
The wait never clicks, navigates, changes storage, captures an answer, or retries Send.

## Codex Chrome connector fallback

Use this fallback only after a qualifying pre-submit DevTools failure and after verifying that the Codex Chrome extension is installed and enabled:

1. Open the extension details and enable **Allow access to file URLs**.
2. Start `waitForEvent("filechooser")` before clicking ChatGPT's visible attachment button and **Upload from computer** item.
3. Call `chooser.setFiles([absolute_path])`.
4. Verify the filename or attachment chip.
5. Pass `--upload-control codex-chrome-visible-menu` to preflight and exchange capture.

The `waitForEvent("filechooser")` plus menu-item click sequence belongs only to this
connector fallback. Do not copy it into the Chrome DevTools route: DevTools must use
one attachment-control click, a fresh menu snapshot, and a direct `upload_file` call
on the menu-item UID. If either route leaves the chooser state unknown, stop and keep
native-chooser cleanup separate from exact staged-file cleanup.

Use semantic visible controls. Treat a hidden input as an implementation detail, not a click target.

## Failure routing

- If DevTools MCP is absent from the current task, fails to initialize or connect, or fails before any DevTools upload action/chooser could start while the composer remains empty, capture diagnostics and try the connector once under the same conversation claim.
- If `upload_file` fails, returns an unknown/error result, or the attachment chip is absent, record a possible native chooser residue and stop all upload/Send retries. Do not claim browser UI cleanup without same-host proof; clean the exact digest-bound staged file separately. If ChatGPT begins the upload but rejects, stalls, or returns a service error, record that remote failure and stop. Changing routes does not change the server-side result.
- If Send may have been accepted, preserve the pre-submit boundary and inspect the exact conversation. Never resend to test the adapter.
- Release the claim in every proven terminal path. A bootstrap `send-accepted` release is rejected until promotion succeeds. When a staged file exists, release through the exact-file cleanup arguments. After promotion and release, use native `read_thread`; do not retain a live conversation claim while GPT Pro generates.
- After MCP/VS Code reload, reacquire the same claim, run `list_pages` and `resolve-tab`, read only requested owners, then take a fresh snapshot of the resolved page and run `check_browser_recovery.py` with the complete page/owner JSON. A resolver HOLD or owner/URL mismatch proves only an observation conflict; it does not prove that Chrome navigated to Project home. Do not navigate, reload, or open a page to repair that inference. Re-read `location.href` and owner on the same pageId and report only the observed mismatch unless direct same-page observations establish a transition. A bound bootstrap never opens another page; `promote-ready` becomes `promote-bootstrap-do-not-resend`. Never reuse stale page IDs or snapshot UIDs. A submitted state is reusable only when the exact user-turn ID is proven after the saved boundary and its prompt SHA matches. Recovery reuses the saved model-control preflight and must not reopen or reconfirm model/thinking controls.
