# 更新日志

## 1.0.0 — 2026-10-24

首个独立发布版本：把 Codex Remote Bridge 的通用基础设施复用为 **DeepSeek Harness** 远程桥。

### 执行层（新增，独立实现）

- 通过官方 SDK stdio JSON-RPC 驱动真实 Harness 运行时：`initialize`、`session/prompt`、
  `session/resume`、`session/wait`、`session/working-directory/get|set`、`session.event`、
  `session.status`。
- 桥生成自己的 profile 补丁（`--patch`），挂载宿主适配器与官方 `dsh-tool-ask-user`；
  不改用户全局 profile，不打包 Harness 安装件。
- 会话所有权：会话被其他 Harness 进程持有时返回 `409 session_busy`，且不写入任何内容。
- 终态由 `session/wait` + `turn/end.reason` 判定；HTTP 200 与 prompt 回执都不算完成。

### 宿主适配器（新增，`adapter/index.js`）

- 仅用 `node:` 内置模块与 Harness 公开 Cordis 服务，通过 `--patch` 注入。
- loopback HTTP + 每进程随机端口与令牌：余额、会话列表、完整历史、待批准、待回答。
- 作为 `approval/request` 与 `user-questions/request` 的 answerer，把人类决定送回原请求。

### 余额（替换额度）

- 右上角从 Codex 的订阅额度改为原生账户余额：充值钱包与赠送钱包分列，多币种不相加，
  十进制字符串原样保留，失败显示「余额暂不可用」而不是 0。
- 金额展示只做四舍五入到两位，原始字符串仍保留在 `entries[]`。

### 页面

- 复用上游的网页结构、Markdown 安全渲染、附件待发区与移动端布局。
- 左上角 LOGO 变成后端切换入口（`role="menu"`）；Codex 项按契约渲染为禁用，不提供无效跳转。
- 所有请求经 `bridgeFetch` 走可配置基址；页面可挂 `/` 或 `/dsh/`，前缀外路径一律 404。
- 新增 `?thread=<sessionId>` 深链。
- 新增工具卡片（参数/结果/状态）、批准与提问通知、推理折叠、每轮用量。

### 未包含

- 取消、steer、模型选择、语音、飞书、云端程序：本版不含，能力接口如实返回 `false`。
