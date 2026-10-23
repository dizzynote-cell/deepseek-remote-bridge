# 架构与实现边界

## 进程模型

```text
python bridge.py
  ├─ HTTPServer（ThreadingHTTPServer，仅本机回环）
  ├─ RuntimePool
  │    └─ 每个模型路由一个子进程：
  │         "<DSH_EXE>" --expose-internals "<DSH_CLI>" --profile sdk --patch <state/runtime/patch-*.yml>
  │         环境：ELECTRON_RUN_AS_NODE=1（DSH_HOME 默认不动）
  │         stdin/stdout：一行一帧 JSON-RPC
  │         插件：adapter/index.js（loopback HTTP，随机端口 + 令牌）
  └─ 后台线程
       ├─ InteractionBridge：1s 轮询适配器上的待批准/待回答
       └─ TaskRunner：每个提交一个 worker，等 session/wait 后判定终态
```

## 为什么需要宿主适配器

官方 SDK wire 只提供 7 个方法与 4 个通知：`initialize`、`session/prompt`、`session/resume`、`session/wait`、`session/working-directory/get|set`、`shutdown`，以及 `session.event`、`session.status`、`subagent.*`。它刻意不覆盖：

- 账户余额；
- 会话列表与历史（历史只在落盘日志里，不会重放）；
- 工具批准与用户提问（wire 没有 server→client 请求）。

因此桥把 `adapter/index.js` 通过 `--patch` 挂进**自己的** sdk profile 组合：它只调用 Harness 已有的公开 Cordis 服务（`deepseekAccount`、`sessionQuery`）与两个 waterfall 事件（`approval/request`、`user-questions/request`），不复制、不改写 Harness 源码。用户全局 profile 与桌面端不受影响。

## 历史与终态

- **历史**：`ctx.sessionQuery.readSession(id)` 返回 `{session, inheritedEventCount, events}`，桥在 Python 侧投影成 turn/item（见 `app/history_view.py`）。投影是纯函数，测试用合成 fixture。
- **过滤**：只把 `user/message` 且 `source.kind == "user"` 的输入当作人类消息；`working-directory`、`runtime-context`、`skill-catalog` 属于 Harness 上下文，不冒充用户发言。
- **终态**：提交后 worker 调用 `session/wait`，再用会话日志里的 `turn/end.reason.kind` 判定 `completed / interrupted / cancelled / failed`。HTTP 200 或 prompt 回执都不算完成。
- **追加输入**：运行中再次提交会被 Harness 排到下一步（`queuedInput`），桥标记 `steered: true`，不重发。

## 所有权与并发

Harness 用跨进程写锁（Windows 命名信号量，POSIX `flock`）保护同一会话的写入。桥的做法：

1. 首次向某会话发送前 `session/resume`；失败且错误含占用语义时返回 `409 session_busy`，并保证**没有**写入任何内容。
2. 会话在本运行时打开后，锁由该运行时持有，直到桥关闭。
3. 不同会话可并行；同一会话的第二个任务被排到下一次 `session/wait` 之后。

## 余额

`ctx.deepseekAccount.getBalance({version, locale, timezoneOffsetSeconds})` → 平台 `/api/v0/users/get_user_summary`。
返回 `{status:"ready", value: normal_wallets, bonusWallets}`，每项 `{currency, balance}`（十进制字符串）。
`null` 表示未登录/凭据被拒；桥映射为 `available:false` + `status:"auth_required"`，绝不折算成 0。
多币种只分别展示，不求和；缓存默认 60 秒，刷新失败保留旧值并标 `stale`。

## 界面与基址

- 静态资源全部相对路径；请求走 `web/bridge-client.js` 的 `bridgeFetch`。
- 服务端把 `DSH_BRIDGE_BASE_PATH` 之外的所有路径当 404，挂载 `/dsh/` 时不会串到同源的其他桥。
- 深链 `?thread=<sessionId>` 直接打开会话。

## 安全默认值

- 只监听回环；非回环地址不在默认配置内。
- 写请求要求 `Origin` 与 `Host` 同源（缺失 `Origin` 的本地工具调用放行），并校验 `Sec-Fetch-Site`。
- 附件只写入桥自己的 `state/data/uploads/<sessionId>/`，名字做白名单清洗；下载接口只允许工作区白名单与上传目录内的文件。
- 令牌不与浏览器交互；适配器端口每次启动随机。
