# 发送检查点与等待恢复

每轮发送使用 `manage_bridge_attempt.py` 保存运行状态，位置为
`.codex/codex-pro-bridge/attempts/<bridge-thread-id>/<attempt-id>.json`。
检查点保存完整问题、摘要、预检结果、owner、对话边界、可选业务截止时间及固定的远端 turn。
它是可恢复的操作状态；原始回答及核验仍由原有三种 canonical events 记录。

## 首次发送

1. 保存完整问题文件，用将实际发送的原文计算 SHA-256，保留换行。
2. 照常运行 `check_browser_preflight.py`，保留成功返回的 JSON。
3. 创建检查点；仅当父代理或用户明确给出真实业务截止时间时才传入
   `--deadline`，且必须带时区。无人值守且没有业务截止时间时省略该参数；这不
   会创建观察截止时间，后续等待/工具/子代理运行上限只触发同一 attempt 的续接。

   ```bash
   python3 ${CODEX_HOME:-$HOME/.codex}/skills/gpt-pro-question-window/scripts/manage_bridge_attempt.py \
     --repo <repo> --bridge-thread-id <thread> prepare \
     --prompt-file <prompt-file> --preflight-file <preflight-json> [--deadline <ISO-8601>]
   ```

4. 保留返回的 `attempt_id`。填好问题并完成原有页面核对后，紧接点击 Send 之前执行：

   ```bash
   python3 ${CODEX_HOME:-$HOME/.codex}/skills/gpt-pro-question-window/scripts/manage_bridge_attempt.py \
     --repo <repo> --bridge-thread-id <thread> send-started --attempt-id <attempt>
   ```

   仅本次成功返回后点击一次。重复执行会拒绝；若命令返回丢失或之后中断，读取检查点并核对原页面。
   `send-started` 表示发送结果可能未知，不能凭输入框为空或等待超时再次发送。

5. 新对话照常在同一 owner 页面完成 `promote-bootstrap`。读取发送后的用户消息 ID、
   摘要和相对原边界的位置，运行 `check_browser_recovery.py --attempt-id <attempt>`。
   对 `submitted` / `generating` / `completed` 的核对成功会自动更新检查点并固定 turn；
   promotion-ready 本身仅授权提升身份，不证明是哪条用户消息。
   若已释放发送 claim，重新获取同一对话的短期 claim 来完成该核对，再释放。
   原有页面身份、附件和 fresh snapshot 检查继续适用。

检查点的 `submitted_at` 只记录实际观察的时间，不以重启或保存时间补写。
已保存 recovery JSON 且知道准确发送时间时，可用 `submitted --attempt-id <attempt>
--observation-file <recovery-json> --submitted-at <ISO-8601>` 补入；不知道时保持空值。

## 根据当前宿主选择等待方式

先查看本轮真实暴露的工具及其说明，仅在确认工具能读取 **ChatGPT 网页对话** 时填写
`chatgpt_read_tool`。Codex 本地线程读取/等待工具不能充当网页对话读取器。
能力 JSON 使用当前工具清单中的完整名称；未暴露的角色留空。例如只有浏览器读取工具时：

```json
{
  "available_tools": ["mcp__chrome_devtools__evaluate_script"],
  "browser_read_tool": "mcp__chrome_devtools__evaluate_script",
  "browser_wait_tool": "mcp__chrome_devtools__evaluate_script"
}
```

`browser_wait_tool`只用于支持异步函数、明确pageId的当前Chrome DevTools evaluate_script接口；它等待DOM变化，不安装新MCP。没有这种能力则留空。
有宿主支持的当前任务唤醒及停止工具时，另填 `watcher_create_tool` 和 `watcher_stop_tool`。
这些字段记录已检查的真实能力，不安装服务，不把工具名当作已经运行的 watcher。

```bash
python3 ${CODEX_HOME:-$HOME/.codex}/skills/gpt-pro-question-window/scripts/manage_bridge_attempt.py \
  --repo <repo> --bridge-thread-id <thread> wait-plan \
  --attempt-id <attempt> --capabilities-file <capabilities-json>
```

- `active-session / wait-exact-turn`：用下述配方在同一活动工具编排内等待；无变化的浏览器批次不返回模型、不写检查点、不反复领锁。只读等待前以短期claim完成owner、URL和pageId解析，随后释放；每次检查仍核对原owner及精确URL，捕获时再领claim并取fresh snapshot。
- `active-session / read-exact-turn`：没有可用异步浏览器接口时，复用原读取工具及返回的间隔，在工具编排内处理无变化结果；不能把Codex线程读取器当成网页读取器。两条路线都需要活动调用，不能宣称final或重启后会自动唤醒。
- `manual` / `needs-reader`：当前宿主缺少可用读取工具。保存检查点，说明缺少的能力。
- `recover-owned-tab-and-promote`：先恢复 bootstrap 身份，再读取目标对话。
- `record-timeout`：仅在检查点存在父代理明确提供的业务截止时间且该截止时间已到时，才用
  `fail --reason <实际诊断>` 持久记录等待终止。没有业务截止时间时不会返回
  `record-timeout`；工具/邮箱/子代理运行上限只要求同一 Agent/attempt 的
  `continuation-required` 续接。任何情形的超时都不等于网页发送失败，发送过的检查点仍阻止
  下一次发送；以后可以核对原 turn 并捕获其答案。

要使用真实宿主 watcher，按以下顺序进行：

1. `reserve-watcher --attempt-id <attempt> --capabilities-file <file>` 先固定唯一登记意图。
2. 调用该宿主的当前任务唤醒工具，任务内容包含 repo、thread、attempt、URL、turn ID、
   问题摘要、（如有）业务截止时间及本参考中的终止规则。不得改成独立 cron 或新 worktree 任务。
3. 成功取得真实 automation ID 后运行 `register-watcher --attempt-id <attempt>
   --automation-id <returned-id>`。只有此时才报告后台等待已登记。
4. 登记调用结果丢失时，`wait-plan` 返回 `reconcile-watcher-registration`。
   查回同一宿主任务并登记其 ID；不能再次创建。如果宿主无法查回，报告缺失的登记结果并暂停。
5. 捕获完成、显式失败、显式业务截止时间到达或宿主要求续接时，先保存回答或诊断，再用真实宿主工具停止该 automation。
   成功后才运行 `watcher-stopped --automation-id <same-id>`。停止失败保留 ID；可用暂停能力时
   暂停它，并报告一次清理失败。这个命令仅记录实际停止结果，不代替宿主停止操作。

`wait-plan` 是只读决策，不执行浏览器调用，也不是后台进程。
VS Code/WSL 停止后自动继续取决于执行宿主实际可提供的唤醒能力。

### 活动工具等待配方

已固定submitted turn并解析原页后，用`manage_bridge_attempt.py --repo <repo> --bridge-thread-id <thread> wait-script --attempt-id <attempt> --observed-page-url <resolve-tab返回的URL> --exec-page-id <page-id>`生成`functions.exec`程序。程序只包含身份、期限和通用读取逻辑，不包含问题或回答正文；省略`--exec-page-id`则返回单次浏览器调用的function JSON。

在同一次`functions.exec`中调用该命令，检查其退出码，再执行生成程序：

```javascript
const prepared = await tools.exec_command({cmd: command, max_output_tokens: 6000});
if (prepared.exit_code !== 0) throw new Error(prepared.output);
await new Function('tools', 'text', 'return (async () => {' + prepared.output + '})();')(tools, text);
```

`command`是上面的显式CLI命令，参数按shell规则引用。浏览器内部每秒读取一次，每批最长55秒；`pending`由生成程序继续处理，只返回可捕获、身份变化、目标不可见、歧义或父代理明确提供且已到达的业务截止时间。未提供业务截止时间时，宿主/工具批次上限只要求同一 attempt 续接，不会产生终止结论。宿主yield时保留原cell并继续wait，不重新发起整条配方。页面导航、MCP断开、未知返回格式交回原恢复流程，不自动重发问题。`ready-for-capture`只触发原Copy reply捕获及核验，不证明全文已保存或科学结论成立；实际响应错误仍需查看原页。等待结束不会自动停止Pro或Codex会话。

同一轮由一个执行者负责准备、发送、等待、捕获及释放。已成功的准备或发送步骤直接复用；出现claim冲突先读取现有owner和检查点，不能用重复acquire或固定sleep尝试夺锁。

## 捕获与重启

固定 turn 的完整回答已完成且无截断后，用原有 `save_bridge_turn.py`，增加
`--attempt-id <attempt>`，同时传 `--web-url`、`--remote-turn-id`、实际
`--response-completed-at` 和 `native-read-thread` / `browser-fallback` 路由。
有 attempt 时，保存逻辑以该 attempt 的 preflight 中已验证的可见暂存文件名和
SHA-256 为唯一附件 provenance；它不再把安全暂存名与源 bundle basename 比较。
名称或 digest 漂移必须失败，不能降级为 `mismatch` 后继续写入成功记录。
浏览器 fallback 继续传入同页 owner/URL/claim/snapshot；原始问题文件必须与发送原文相同。

捕获先固定答案摘要，重试不会因观察时间变化生成第二份 exchange。
文件与 canonical ledger 均写入后才标为 `captured`；不同 turn、不同答案或其他轮次的文件不能
完成本轮检查点。之后另行记录 Codex verdict，不能把捕获等同于本地核验完成。

重启时用 `status --attempt-id <attempt>` 读取状态，再走同一 owner 的 `resolve-tab` 和
`check_browser_recovery.py`。已有未完检查点会被预检与恢复入口自动发现；不得另建 attempt 绕过它。
resolver 的 HOLD 或 owner/URL 不一致只证明观察冲突，不证明页面返回 Project 首页。不得据此
导航、刷新或打开新页；应在同一 pageId 重新读取实时 `location.href` 与 owner。只有直接的同页
观察确实显示 URL 变化时才能陈述“页面发生导航”。恢复沿用 attempt 中已通过的模型控件
preflight，不再打开、调整或重复确认模型与思考强度。
旧的无检查点历史轮次仍可按原合同捕获，但不能由此推断它具有自动恢复或后台唤醒能力。
