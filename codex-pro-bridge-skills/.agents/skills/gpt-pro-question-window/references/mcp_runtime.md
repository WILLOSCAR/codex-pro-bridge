# 程序化执行与持久任务 MCP

本运行时把固定步骤移入 Python worker，通过现有 Chrome DevTools MCP 操作浏览器。
身份解析、模型和上传回执、attempt 与交换账本仍由原有 helper 校验。
worker 不调用语言模型；主代理仅接收状态和结果文件路径。

## 可用范围

- 接受父代理通过 `prepare_bridge_execution.py` 发布的 `executor_handoff/v2`。
- 支持 `explicit`、`auto`、`none`，复用材料准备、暂存和 SHA-256 校验。
- 支持已核验的 Project 和 standalone、新会话 bootstrap，以及已有绑定会话续聊。
- 初次 Project 身份核验、Sources inventory 修复仍使用 Project skill，完成后再提交任务。
- UI profile 必须来自当前浏览器实际观察。支持独立模型/强度菜单，以及显式配置的
  `control_layout=nested-slider`。后者提供 `thinking_slider`、`thinking_keyboard_control`
  和 `thinking_positions`（可见强度名称到已观察位置的映射）。通过键盘逐格调整并回读位置、
  最终可见文字；不猜坐标，不编辑网页内部状态，不把“6 Pro”之类强度标签解释成模型。
- 嵌套菜单使用 `model-controls/v2`：模型子菜单初始与最终各观察一次（总计两次展开），
  模型已匹配时零次选择；外层菜单为两次观察，加至多一次强度调整。仍禁止预检后重复确认。
  独立菜单保留 v1 合同。位置映射失效、焦点失败、单步不生效或标签不符均停止，不重复按键。
- Windows 原生和 WSL→Windows 使用已有 host staging 配置；Copy reply 使用 Windows 剪贴板。
- 当前自动执行适配器依赖各浏览器工具显式支持 `pageId`，不使用全局选中页。

## 安装与配置

入口：`scripts/bridge_mcp.py --config <private-runtime.json>`，Python 3.10+，运行时只依赖标准库。
将包内 `config/bridge-runtime.json.example` 复制到私有位置，填写绝对状态目录、授权仓库根目录、
浏览器 MCP argv 和基于实际观察的 UI profile。不把这些主机值提交到仓库。

`browser_command` 指向 Chrome 所在宿主的 MCP；worker 在一次任务内复用同一进程。
推荐显式设置 `browser_transport=persistent`：在私有 `state_dir/browser-connections/` 中
按浏览器命令、环境、profile 和超时配置识别一个共享连接服务，不按 worker 新建浏览器 MCP。
服务仅转发调用，不选择页面、不改写问题、不做 Send 或上传重试；身份与 attempt 门仍由 worker 负责。
不同 worker 退出不关闭已授权连接；Chrome 退出、服务故障或显式断开后仍可能需要重新授权。
省略该配置或设置 `stdio` 保留每个 worker 自建连接的兼容路径。
可直接使用已安装的同版本 Node 与 MCP 入口组成 argv，避免每轮 `npx` 的包解析及更新检查；
入口路径必须显式配置，不扫描缓存选择任意版本，不自动升级 MCP。直接启动只减少启动耗时，
不绕过 Chrome 的远程调试授权。退出时先关闭 stdin，让 MCP 按 EOF 正常断开浏览器；
只有未正常退出才逐级终止其启动进程，不关闭用户标签页。
stdio 路径的 `browser_connect_timeout_seconds` 默认 300 秒，仅用于首次只读 `list_pages` 触发的连接；
`browser_tool_timeout_seconds` 默认 90 秒，用于其他调用和连接后的读页。persistent 服务保留首次
只读授权等待，不因调用方的等待超时或进程退出取消这个请求；之后的工具调用仍有超时，未知结果
阻止继续调用，不自动重连或重放。初始化成功不等于
浏览器连接完成；job 的 `browser_phase` 区分 `connecting-browser` 与 `connected`。
两项超时均不会重试上传或 Send，也不是网页回答的业务 deadline。Chrome 的授权仍须用户完成。

共享服务只监听本机 loopback，使用私有随机凭据和 JSON，不反序列化 pickle。
`state_dir` 必须只有当前用户可读写；Linux 服务叶目录要求 0700，Windows 应使用当前用户私有 ACL
的目录。勿把该目录放入共享盘、提交仓库或输出其中的 `endpoint.json`。维护代码可通过
`SharedClient.status()` 查看状态；只有明确要求断开时调用 `shutdown()`，它不关闭 Chrome 页面，
且拒绝打断正在执行的普通工具动作。运行中的服务不会热加载源码修改。
`browser_env` 只作用于浏览器进程；staging 的配置变量须由 Bridge MCP 启动环境提供。
多个 Codex MCP 实例必须共用同一个 `state_dir` 和 browser ownership registry，
才能共享任务去重和剪贴板互斥；不同主机不能通过这个目录获得跨主机互斥。
`pageId` 只属于当前浏览器 MCP 连接；重连后必须重新列页并通过 owner + URL 定位，
不能把另一连接的数字 ID 直接传给新连接。

可选 Codex 配置：

```toml
[mcp_servers.codex-pro-bridge]
command = "<absolute-python-executable>"
args = ["<absolute-skill-path>/scripts/bridge_mcp.py", "--config", "<absolute-private-runtime.json>"]
tool_timeout_sec = 90
```

## 调用顺序

1. 冻结证据、目标、模型、强度和 Send 授权，运行现有准备 helper。
2. `bridge_submit(handoff_path, handoff_sha256)`，保留返回的 `job_id`。
3. `bridge_wait(job_id, seconds=55)` 可长轮询，其他任务同时可调用 status。
   单次 wait 结束只返回当时状态，worker 继续运行；不得因此重建任务。
4. 完成后调用 `bridge_result(job_id)`，它重新校验原始 answer 与 canonical turn 的摘要。
   主代理读取文件，核验答案并独立记录 Codex verdict。
5. MCP 重连或 worker 中断时用 `bridge_status` 检查；`bridge_resume` 沿用原任务恢复。

同一个 handoff 摘要重复 submit 返回同一个任务；重复 resume 不会产生第二个执行者。
回答正文保存在工件中，不在 MCP 状态消息里返回或转述。

## 中断语义

任务 envelope 只记录调度、材料产物和浏览器阶段；Send 是否发生只信任 canonical attempt。
没有显式业务 deadline 时，worker 不设置整轮 15/30 分钟截止时间。

- `send-started` 后恢复只读取原 owner 页面并验证保存边界之后的唯一 prompt/turn，不再次点击 Send。
- 用户消息的可见文字可能因行内代码等 Markdown 渲染丢失标点。此时仅对边界后唯一候选消息
  使用可见 Copy message 按钮，在剪贴板互斥锁内读取原文、核对内容与冻结 prompt 精确一致，
  再固定远端 turn；保留复制原文与摘要回执。禁止通过删除标点或模糊匹配放宽身份检查。
  复制控件缺失、剪贴板变化无法确认、消息或 owner/URL 变化均停止，不重发。
- 在上传、填写草稿或 `prepared` 后中断时，自动恢复不重放副作用，返回具体待检查阶段。
- 上传调用返回后立即保存 `upload-result.transport.json`，再检查附件卡片。若停在
  `upload-started` 且已有该回执，可只读恢复：核对同一 page/owner/URL、上传计划、原包与
  暂存摘要、唯一同名且非忙碌的附件、空草稿，再重新预检。不会再次调用上传；任何缺失或冲突均停止。
  旧版未留下上传调用回执的任务不适用，不从页面文件名或异常文字补造回执。
- 浏览器身份变化、原生 chooser 未知、非唯一控件或捕获结构不匹配均返回 `blocked`。
- 已提交的短浏览器 claim 在等待前释放，捕获时重新领取。页面不关闭、不重载。
- worker 可跨父 MCP 的 stdin 断开继续运行；宿主关机/进程被杀后需要同一 job 的 resume。
  `worker_recently_alive` 是最近心跳，不等于网页有进展。

只有任务尚未开始（queued）、已经有 canonical attempt，或停在
`prepared-materials` 且尚未开始浏览器动作（未记录 browser phase、`acquiring-claim` 或
`connecting-browser`），或满足上述完整上传回执恢复条件时才支持自动 continuation。
claim 冲突必须先由其原 owner 正常结束；恢复不抢占、不清除其他任务身份。
连接阶段恢复复用原始包与摘要，再经 canonical resolver 定位页面，不重新打包。
一旦进入 `browser_phase=connected` 后的模型、上传或草稿阶段，仍禁止无凭据重放。
发生 pre-Send 阻塞时保留草稿、暂存路径与 claim 供检查，不能宣称 UI 已清理。
不得在一个未解决 job 上同时派旧 Executor 再跑一轮。

## 验证边界

`test_bridge_runtime.py` 通过浏览器替身驱动真实准备、预检、bootstrap promotion、attempt 和账本，
并测试同一 handoff 并发提交、Send 后断连禁重发和 immutable result 校验。
这证明程序与协议的衔接；不替代当前 ChatGPT UI 的真实上传和模型控件验收。
运行时未通过当前宿主的真实验收前，不把它设为已有生产线程的默认执行路径。

Windows 冻结工件使用 UTF-8 原字节写入，不进行 LF→CRLF 自动转换。准备回执中的 request
和 handoff 摘要必须等于实际落盘文件的 SHA-256，不能仅验证文本解码后的内容相同。
