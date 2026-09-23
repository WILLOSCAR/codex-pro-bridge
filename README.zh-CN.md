# Codex Pro Bridge

[English](README.md)

## Introduction

Codex Pro Bridge 面向在算法设计、研究推理和工程实现之间来回切换的工作。

Codex 和 GPT Pro 都有用，但它们适合待的位置和工作的节奏并不一样。

Codex 更适合留在本地仓库里。它能读文件、改代码、跑测试、看日志，并检查一个方案是否真的符合当前实现。

GPT Pro 更适合慢一点的外部推理：质疑算法、梳理 failure mode、设计 ablation、规划实验、组织论文 framing，或者尝试从反面推翻一个想法。

真正脆弱的是两者之间的交接。

### 问题

没有桥接层时，流程很容易变成在仓库、Codex 和浏览器之间来回切换，再靠手工复制粘贴把它们连在一起。

外部模型不会自动知道当前仓库状态、实际实现、本地实验，以及 Codex 已经做过的判断。

补充这些信息很快会变成“上下文太少”和“上下文太多”之间的选择。

上下文太少时，reviewer 会对没有看过的代码给出自信建议。上下文太多时，材料噪声大、更新困难，也更容易混入无关或敏感内容。

### 工作流痛点

这次交接会产生几类损失：

- **注意力损失：**频繁在仓库、Codex 和浏览器之间切换。
- **结构损失：**prompt、文件、假设、追问和决策在工具间复制时逐渐散掉。
- **可复现性损失：**之后很难知道 GPT Pro 当时到底看到了哪些证据。
- **状态漂移：**Web 对话继续往前走，但真实仓库、实验和实现已经发生变化。
- **落地损失：**一个不错的算法或研究想法没有回到测试、配置、日志和真实代码里。

在单轮里，这些只是流程摩擦；经过多轮后，它们会让评审难以复用、审计和实现。

缺少的不是另一个聊天窗口，而是本地执行与外部推理之间可复现的交接方式。

### 核心思路

Codex Pro Bridge 不是 API client，也不是让 GPT Pro 直接修改本地文件。

它是由 Codex skills 和辅助脚本组成的本地工作流层。它把一次 GPT Pro Web 评审变成本地工程材料：可以复用，可以追溯，也能重新接回真实仓库。

这条工作流是：

1. Codex 先把请求路由到本地执行、独立评审或当前仓库绑定的 ChatGPT Project。
2. Codex 围绕一个具体任务写本地 notes。
3. Codex 生成带明确证据边界的 bundle。
4. GPT Pro 在正确的 Web 对话中评审限定范围内的材料。
5. Codex 保存完整回答，做本地摘要，并验证可执行结论。
6. 同一个任务继续进入实现、实验或下一轮聚焦评审。

在 Codex 桌面端，Bridge 会在 Send 后释放浏览器，并在能力可用时通过原生读取跟踪准确的 ChatGPT 对话与目标轮次。可选的一次性 heartbeat 会在等待期间保持静默，并在完成、失败或超时后释放；若原生结果被截断，则重新获取浏览器 lease，回收同一目标轮次的完整回答。

```mermaid
sequenceDiagram
  participant C as Codex
  participant B as Bridge
  participant G as GPT Pro Web
  C->>B: 解析本地、Standalone 或 Project 路由
  B->>B: Project 模式下核验绑定和共享来源
  B-->>C: Thread、会话与证据策略
  C->>C: 检查仓库并写 codex-snapshot
  C->>G: 有证据边界的 bundle + 聚焦问题
  G-->>C: 外部评审
  C->>C: 保存 gpt-exchange 并记录 codex-verdict
  C->>C: 实现、实验或继续同一个任务
```

目标是让这条链路可复用、可审计、可实现。Codex 始终是事实源，GPT Pro 始终是外部 reviewer。

### 稳定性与无人值守

Bridge 将浏览器状态作为明确协议处理，而不是尽力而为的 UI 宏：

| 能力 | 保证 |
| --- | --- |
| 标签页所有权与并行 | host-global 的 profile、Project 和 conversation claim 将一个 Bridge Thread 固定到一个 owned tab；互不相关的对话可以并行，且不关闭页面 |
| 新建会话 | 从 Project/home bootstrap 首次成功 Send 后，立即提升到准确、稳定的 conversation URL |
| 禁止重复发送 | durable attempt 记录 preflight、`send-started`、Send 接收状态和固定 remote turn；未知发送结果只能恢复，不能重发 |
| 无人值守恢复 | wait plan 与 continuation receipt 在运行时让出后继续沿用同一 attempt，不把本地超时误当成任务结束 |
| 上传安全 | `devtools-upload/v1` 只允许一次附件控件点击、一次新菜单快照和直接 `upload_file`；chooser 状态未知时 fail closed |
| 模型控件 | `model-controls/v1` 将模型与思考强度分开核验，支持“最新”等动态标签，并限制读取和调整次数 |
| 准确回答捕获 | 固定 remote turn 并保留 Copy reply 的 Markdown 结构；纯文本回退会明确标为降级捕获 |
| Project 切换 | 显式 rebind 会归档旧 Sources manifest，任何变更前必须重新取得完整远端 inventory |
| 确定性交接 | `executor_handoff/v2` 在浏览器工作前冻结问题、证据策略、目标、授权、模型控件、输出位置和摘要 |

可选的 `bridge_executor` agent 负责打包、暂存、上传、等待和原始回答捕获，减少主代理上下文占用；证据选择和最终 verdict 仍归主代理。可移植模板位于
[`agents/bridge-executor.toml.example`](codex-pro-bridge-skills/agents/bridge-executor.toml.example)。

## 工作方式

每个任务使用一个 bridge thread，使证据、外部评审、本地 verdict、实现和后续 follow-up 保持在同一条任务链上。

### 执行范围

同一套任务协议支持三种执行范围：

| 范围 | 结构 | 适合场景 |
| --- | --- | --- |
| `local_only` | Codex 直接完成 | 不需要外部评审 |
| `standalone` | 一条 Bridge Thread 对应一个 GPT 对话 | 小项目、一次性评审 |
| `project` | 一个仓库 Project 下包含多条任务 Thread | 有共享资料、多会话的长期科研项目 |

### Project 模式

Project 模式是在原有 Bridge Thread 工作流上增加的一层可选组织结构。当一个
仓库里同时存在多项长期任务、多个 GPT 对话，以及一批需要跨对话共享的背景资料
时，可以使用 Project 模式；小项目仍然可以继续使用 standalone。

```mermaid
flowchart TB
  LR["本地仓库"] --> BP["Bridge Project"]
  BP ==>|已核验的一对一绑定| CP["ChatGPT Project"]
  BP --> PS["Project Sources<br/>稳定共享上下文"]
  BP --> T1["Bridge Thread A<br/>任务身份"]
  BP --> T2["Bridge Thread B<br/>任务身份"]
  T1 --> A1["Codex snapshots<br/>Task Bundles<br/>Codex verdicts"]
  T2 --> A2["Codex snapshots<br/>Task Bundles<br/>Codex verdicts"]
  T1 ==>|主要对话| G1["GPT Conversation A"]
  T2 ==>|主要对话| G2["GPT Conversation B"]
  PS -. 共享给 .-> G1
  PS -. 共享给 .-> G2
```

这层关系刻意保持简单：一个本地根目录最多有一个 Bridge Project，一个 Bridge
Project 最多绑定一个当前 ChatGPT Project。Project 名称只是显示信息；只有在
可见的 Project URL、ID、当前账号和 workspace 都核验正确后，自动 Project
路由才会生效。

Project 模式目前提供以下能力：

| 能力 | 具体行为 |
| --- | --- |
| 已核验的 Project 绑定 | 绑定已有 ChatGPT Project 或新建一个 Project，并在使用前核验可见身份 |
| 自动路由 | 在 `local_only`、`standalone` 和 `project` 之间选择；绑定或共享来源异常时暂停提交 |
| 共享 Project Sources | 将 project brief、术语表、PRD、长期决策等稳定资料共享给 Project 下的多个对话 |
| 安全更新来源 | 根据完整远端清单制定计划，检查敏感内容和容量，只修改 Bridge 自己管理的版本 |
| 多任务组织 | 独立交付物使用独立 Bridge Thread 和 GPT 对话，同一任务的后续轮次复用原任务 |
| Project 生命周期 | 记录任务状态和依赖；挂入旧 standalone Thread；支持归档、恢复、解绑和丢失绑定恢复，不删除远端内容 |
| 本地审计记录 | 用本地追加式状态保存绑定、来源、任务和核验事件，不再依赖人脑记住浏览器状态 |

Project Sources 和 Task Bundles 负责不同的事情。稳定的项目背景通过 ChatGPT
Project 在多个对话间共享；diff、日志、局部代码、实验结果和单轮问题仍放在
不可变的 Task Bundle 中，因此每次评审依然有准确的证据边界。

来源所有权采用保守策略。远端原有的文件、instructions 和 conversations 都按
用户管理内容处理。`read_only` 不修改远端来源；`append_only` 添加新的 Bridge
版本并保留旧版本；`managed` 只有在新版本已经可见后，才允许替换 Bridge 自己
管理的旧版本。任何模式都不允许自动删除用户管理的内容。

已经绑定到当前 Bridge Thread 的对话会继续复用。远端 Project 中原有但尚未
纳入 Bridge 的对话仍然属于外部内容，只有用户明确采用后才会绑定；Bridge 不会
在无关对话之间静默猜测，也不会偷偷改绑已经保存的对话 URL。

Bridge Thread 仍然是任务身份，因此 Project 模式不会再重复增加一层 Workstream。
旧的 standalone 历史可以随后挂入 Project，而且不需要改写旧事件。本地 Bridge
状态会保存 ID、摘要、状态、经过核验的观察，以及用户明确纳入当前任务的评审
材料；它不会保存浏览器 cookie、access token 或无关的网页私人响应。

无论使用哪种范围，Bridge 都会把三件事分开处理：

- **证据构建：**只准备足以支持当前决策的最小材料。
- **外部推理：**围绕准确的证据提出聚焦问题。
- **本地验证：**在修改代码或相信结果前，检查每一条可执行结论。

### 证据模式

| 模式 | 适用场景 | 仓库源码 |
| --- | --- | --- |
| `auto` | 第一轮、实现相关评审 | 选择 focus，补保守的本地依赖，再增加相关广度 |
| `explicit` | 聚焦 follow-up | 只重新发送需要评审的文件 |
| `none` | 只需要推理的 follow-up | 复用当前 notes 和任务上下文，不重新发送源码 |

Auto 模式会跟随 JavaScript/TypeScript 和 Python 中可以确定为本地的相对 import，也支持 `.mjs`、`.cjs`、`.mts`、`.cts` 等现代 Node 源码与测试文件。

Follow-up 通常只复用当前 Codex notes 和精简任务历史。只有文件发生变化，或者 reviewer 必须重新检查实现时，才再次发送源码。

## 适用场景

当一个决策值得增加一次独立推理评审时，可以使用 Codex Pro Bridge：

- 评审算法、训练管线、reward 设计或评测方法。
- 压测研究 claim、论文 framing、novelty 论证或 reviewer story。
- 把方案转成 baseline、ablation、metric 和决策规则。
- 检查代码、配置、数据切分、命令、日志和报告结果是否一致。
- 让复杂评审跨越多轮，同时保留证据和决策来源。

对于小型本地 bug、格式修改或直接实现任务，Codex 通常应该直接在仓库内完成，不需要使用 bridge。

## 快速开始

### 使用前准备

第一次使用 Bridge 前：

1. 在 Chrome 所在主机配置 [Chrome DevTools MCP](https://github.com/ChromeDevTools/chrome-devtools-mcp)，打开 `chrome://inspect/#remote-debugging` 启用远程调试，然后重启 Codex。
2. 在 DevTools MCP 将控制的 Chrome profile 中登录 ChatGPT。
3. 可选 fallback：安装 [Codex 扩展](https://chromewebstore.google.com/detail/codex/hehggadaopoacecdllhhajmbjkdcmajg)，并在扩展详情中开启 **Allow access to file URLs**。

以上只需设置一次。每次开始外部评审前，选中的 skill 会再次检查这些前置条件。

仓库和计算任务可以通过 SSH 在远端运行，同时由本地 Bridge 控制本地 Chrome；上传前应将获准的 bundle 落到本机并核对摘要。运行在 SSH 远端的 Codex 不会自动继承本机 Chrome 会话，除非显式配置了安全的浏览器连接，否则应由本地 Bridge dispatcher 负责浏览器步骤。

### 安装

全局安装：

```bash
./codex-pro-bridge-skills/install.sh --global
```

安装到指定仓库：

```bash
./codex-pro-bridge-skills/install.sh --repo /path/to/repo
```

Windows PowerShell 同时支持全局安装和指定仓库安装：

```powershell
.\codex-pro-bridge-skills\install.ps1 -Global
.\codex-pro-bridge-skills\install.ps1 -Repo C:\path\to\repo
```

Windows 原生运行，以及 WSL 侧 Codex 配合 Windows 侧 MCP/Chrome，均可按
[`HOST_TOPOLOGIES.md`](codex-pro-bridge-skills/docs/HOST_TOPOLOGIES.md) 配置带
SHA-256 校验的可选暂存拓扑。仓库中的示例只记录环境变量名；具体路径应保留在
主机私有配置中。

如需委派式无人值守执行，可将 agent 模板复制到
`$CODEX_HOME/agents/bridge-executor.toml`（默认 `~/.codex/agents/`），并先检查其中的模型和 service tier。安装器不会覆盖已有 agent 配置。

如果已有 Codex task 没有发现更新后的 skills，请重启 Codex 或新建 task。

### 可选：程序化 MCP 执行器

本分支包含完整 Bridge、Windows/WSL 安装与暂存适配，以及新的程序化运行时；
可以独立 checkout 和安装，无需先应用其他 PR。已有专用 Agent 路线继续保留。

程序化路线使用 `bridge_submit → bridge_wait → bridge_result`，由持久 worker 执行打包、
上传、发送、等待和原文回收；主代理保留证据选择与最终核验。可选持久连接服务让不同 worker
复用同一个已授权 DevTools 连接，减少重复启动和授权等待。

按 [运行时配置说明](codex-pro-bridge-skills/.agents/skills/gpt-pro-question-window/references/mcp_runtime.md)
填写私有配置；[示例](codex-pro-bridge-skills/config/bridge-runtime.json.example) 不含个人路径。
Windows 原生使用 Windows Python、Node 和路径；WSL 路线使用 WSL Python 与 Windows MCP/Chrome，
暂存映射仍按上述主机拓扑配置。两种方式均需自己的实际 UI profile、Chrome 登录与调试授权，
安装器不会自动启用 MCP 或覆盖用户配置。当前适配器要求 DevTools 工具支持显式 `pageId`。

### 提一个普通问题

```text
Use $gpt-pro-question-window.
Route this task automatically and ask GPT Pro:
<问题>
Capture the raw answer, verify it locally,
and record a separate Codex verdict.
```

这仍然保留原来的 CLI 使用感：skill 会自动选择 Thread 和会话，只有 Project
需要创建、绑定或修复时才要求人工确认。

### 绑定长期 Project

```text
Use $gpt-pro-project-workspace.
把这个仓库绑定到我已有的 ChatGPT Project，
核验可见的 Project 与账号身份，
并规划稳定 Project Sources，不删除用户自己维护的文件。
```

这通常只需要设置一次。绑定生效后，普通外部问题仍然从
`$gpt-pro-question-window` 或其他专用 review skill 进入；当当前仓库和任务已经
属于这个 Project 时，router 会自动选择 Project 模式。

### 维护已绑定的 Project

```text
Use $gpt-pro-project-workspace.
检查当前 binding、tasks 和 Project Sources。
从以下文件刷新稳定共享来源：
<文件>
先预览计划，不要删除用户管理的内容。
```

同一个 skill 也可以更新任务信息、处理缺失或过期的来源、归档或恢复本地 Bridge
Project，以及在远端 Project 再次可见后修复原绑定。

### 运行完整算法或研究闭环

```text
Use $gpt-pro-algorithm-pipeline.
Run the Codex -> GPT Pro -> Codex loop for:
<任务>
Keep one bridge thread, send only scoped evidence,
and implement only locally verified changes.
```

更多示例见 [examples/usage_prompts.md](codex-pro-bridge-skills/examples/usage_prompts.md)。

## Skills

| Skill | 作用 |
| --- | --- |
| `coordinate-auto-research` | 协调 Auto Research 角色、Research Line、本地 Chat、Web 对话 lineage、Handoff 与 Settlement |
| `gpt-pro-project-workspace` | 绑定、路由、同步、查看和修复 Project 工作区 |
| `gpt-pro-question-window` | 提普通问题或继续已有外部评审 |
| `gpt-pro-review-probe` | 对单个想法、方案或原子任务执行高频、可并行的 standalone 评审 |
| `bundle-algorithm-context` | 为需要源码的评审构建限定范围的证据包 |
| `gpt-pro-research-algorithm-reviewer` | 评审算法、管线、实验和研究 claim |
| `gpt-pro-paper-brainstormer` | 推进论文 framing、novelty、反对意见和实验故事 |
| `experiment-plan-generator` | 把想法或评审转成实验矩阵 |
| `implementation-consistency-checker` | 检查方案、代码、配置、数据、评测和结果的一致性 |
| `gpt-pro-algorithm-pipeline` | 运行完整的证据、评审、验证、实验和实现闭环 |

不需要外部推理时，直接在本地使用 `$experiment-plan-generator` 和 `$implementation-consistency-checker`。

## 延伸文档

- [工作流概览](codex-pro-bridge-skills/docs/WORKFLOW.md)
- [Auto Research Skill 优化方案](codex-pro-bridge-skills/docs/AUTO_RESEARCH_OPTIMIZATION.md)
- [Canonical bridge protocol](codex-pro-bridge-skills/.agents/skills/gpt-pro-question-window/references/bridge_protocol.md)
- [Bridge Project protocol](codex-pro-bridge-skills/.agents/skills/gpt-pro-project-workspace/references/project_protocol.md)
- [Evidence bundle schema](codex-pro-bridge-skills/.agents/skills/bundle-algorithm-context/references/bundle_schema.md)
- [AGENTS.md 集成片段](codex-pro-bridge-skills/docs/AGENTS_APPEND_SNIPPET.md)

## Star History

<a href="https://www.star-history.com/?repos=WILLOSCAR%2Fcodex-pro-bridge&type=date&legend=top-left">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/star-history/star-history-dark.svg">
    <img alt="Star history chart" src="assets/star-history/star-history-light.svg">
  </picture>
</a>

## 鸣谢

感谢 [Linux.do](https://linux.do/) 社区提供的讨论、反馈与支持。
