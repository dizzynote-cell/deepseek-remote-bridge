# 独立发行还缺什么：对照官方 SDK 与「手机控制」场景

本文件是**缺口清单与优先级**，不是承诺。每条都区分三种性质：

- **SDK 限制**：官方 wire 没有这个能力，桥不能假装有。
- **可做未做**：接口支持，只是还没实现。
- **产品缺口**：与 SDK 无关，是手机远程使用才暴露的问题。

## 1. 对照官方 SDK（`dsh-sdk-protocol` 的 7 个方法 + 4 个通知）

| SDK 能力 | 桥的现状 | 缺口 |
|---|---|---|
| `initialize`（provider / model / reasoningEffort / maxTokens / cwd） | 全局固定一条路由，`cwd` 每会话设置 | **不能按会话选模型或推理强度**；`maxTokens` 只能改配置重启 |
| `session/prompt`（文本 + 内联图片） | 已用，图片转 durable 附件 | 无 |
| `session/resume` | 已用，被占用时 409 | 无 |
| `session/wait` | 已用，作为终态判定的一半 | 无 |
| `session/working-directory/get` / `set` | 只在新建会话时 `set` | 页面里还不能在会话中途切换工作目录 |
| `session.event`（全量事件） | 文本/推理/工具/批准/用量 + `todo/write` 计划 + 实时状态（step/tool/用时） | 仍未展示：`subagent.*`、`job/*`、`goal/change`、`request/context`、`agent/error` |
| `session.status` | 已用 | 无 |
| `subagent.started/finished` | 忽略（仅写入通知缓冲） | **子代理过程不可见**——长任务里最需要的进度之一 |
| `shutdown` | 已用 | 无 |
| 取消 / 关闭会话 | **SDK 没有** | 只能杀进程（丢当前轮），不做 |

**结论**：执行层没有漏掉任何 SDK 能力；漏的是**把已有事件展示出来**，以及 `working-directory/set` 没接到 UI。

### 可直接补的（不需要官方改协议）

1. 子代理卡片：`subagent.started/finished` 的 `parentSessionId/childSessionId/status/lastAssistantMessage`。
2. 后台任务进度：`job/registered` → `job/settled` 的 `label/startedAt/finishedAt/status/detail`（长命令时最有用）。
3. 目标与轮次：`goal/change` 显示目标、阶段与已用轮次。
4. 上下文用量：`request/context`（contextWindow）+ `assistant/message.usage` 已有，可加「上下文占用」条。
5. 工作目录切换：`session/working-directory/set` + 页面入口（带白名单校验）。

已完成：`todo/write` 计划清单与完成度、实时状态条（轮次/当前工具/工具次数/用时）、
会话内权限预设切换（`permission/preset` + `sandbox/mode` 写入 Harness 日志）。

## 2. 手机控制才暴露的产品缺口

| 缺口 | 影响 | 性质 |
|---|---|---|
| **没有登录/认证** | 只能本机回环使用；一开局域网或反代就是"谁都能看会话、发指令" | 产品缺口（最高优先） |
| **没有 HTTPS / 隧道指引** | 手机在外网无法安全访问；PWA 也无法安装（需要安全上下文） | 产品缺口 |
| **没有推送** | 审批、提问、任务完成只能靠页面轮询；手机锁屏就错过 | 产品缺口 |
| **重启后任务表丢失** | 桥重启后，之前在跑的任务在页面上显示为空闲；历史仍正确（已实测） | 可做未做 |
| **搜索只覆盖已加载页** | 侧栏搜索是本地过滤最近 50 条，旧会话搜不到 | 可做未做 |
| **没有置顶/隐藏/重命名** | 会话多时不好找；旧桥有 | 可做未做 |
| **不能管理多个工作区** | 白名单写死在配置里；手机上无法新增目录 | 可做未做 |
| **没有审计** | 多设备/多人用时，不知道谁在何时发了什么 | 可做未做 |
| **DSH 升级兼容** | 适配器依赖 `sessionQuery` / `deepseekAccount` / 两个 waterfall 事件；官方改名就会失效 | 需要版本自检 |

## 3. 建议的优先级

**P0（不做就不能算"手机能用"）**

1. **飞书 OAuth 登录**（用户已定方案，见 [`feishu-setup.md`](feishu-setup.md)）：不写密码、不存密码、企业身份可撤销。
2. **HTTPS 或可信隧道**：手机访问与 PWA 安装的前提；证书可以由用户自己的反代提供。
3. **Web Push 或飞书通知**：审批/提问/完成三类事件推送，避免锁屏错过。

**P1（体验补齐，工作量小）**

4. 子代理 + 后台任务的可视化（纯前端，数据已经在 `session.event` 里；计划/实时状态已完成）。
5. 工作目录切换（一个 `set` 调用 + 白名单校验）。
6. 重启后任务状态恢复（把 running 任务标记为"已中断，需重发"，而不是静默变空闲）。
7. 搜索走服务端（`sessionQuery` 已支持按标题/文本过滤）。

**P2（可选）**

8. 置顶/隐藏/重命名（桥内本地元数据，不写 Harness）。
9. 多工作区管理、审计日志、Agent Disk 大文件。
10. DSH 版本握手：启动时读 `serverInfo` + 关键服务存在性，版本不匹配就明确报告而不是运行到一半失败。

## 4. 明确不做

- 取消运行中的任务、指定回合 steer、模型热切换：**等官方 SDK**。桥的面板如实显示 `cancel/steer/modelSelection: false`。
- 把模型密钥或 Harness 运行时搬到云端：与官方架构冲突，也不安全。
