# 更新日志

## 1.2.0 — 2026-10-24

托盘图标真正接进启动链路，并补上「图标被 Windows 收进溢出区」的处理。

- `start-bridge.ps1` 与 `DeepSeek Remote Bridge.cmd` 启动桥后自动带起托盘；`scripts/start-tray.ps1`、`stop-tray.ps1` 可单独管理。
- 托盘图标改为运行时绘制的绿色圆角 **D**（不随仓库分发二进制）；双击打开页面；菜单新增「打开日志目录」「任务栏显示设置…」。
- 托盘写 `state/logs/tray.log`：启动、图标创建、状态变化、退出都有记录，便于排查「没有角标」。
- `state/tray.pid` + 进程身份校验（PowerShell + 绝对 `tray.ps1` 路径），与桥的 PID 规则一致，不会误杀 Codex 桥。
- `scripts/show-tray-settings.ps1` 打印固定步骤并打开 Windows 任务栏设置。
- `status-bridge.ps1` 同时报告托盘状态。
- Windows 10 默认把新图标放进 `^` 溢出区，这是系统行为；文档与脚本给出固定方法。

## 1.1.1 — 2026-10-24

补上两项此前只有设计没有实测的行为，并把结果写进验证记录。

- 新增 `tests/live_parallel.py`：两个会话真正同时运行（列表里同时 `bridgeRunning`），各自写各自的文件；
  空闲会话的后续输入 `steered:false`，运行中的第二个输入 `steered:true`，两条都完成且不重发。
- 新增 `tests/live_files.py`：模型写出的 `.md` 与 `.csv` 作为返回文件出现，下载返回正确类型；
  白名单外路径（例如凭据文件）返回 403，白名单外目录新建会话返回 403。
- 版本号只随测试与文档变化，功能集不变。

## 1.1.0 — 2026-10-24

可选语音朗读，以及在 `docs/` 里补齐给 Codex 的部署与回退说明。

### 语音（新增，可选）

- `app/voice.py`：标准库直连 TTS 的 SSE 接口，合成 MP3 并缓存在桥自己的 `state/cache/voice/`；
  键为 `SHA-256(JSON.stringify([text, voice, resource, "v3-sse-mp3-rate25"]))`，与页面实现一致。
- 端点：`/api/voice/config`、`/api/voice/status`、`/api/voice/request`、`/api/voice/audio/<key>`、
  `/api/preferences`（开关）。
- 页面：完成轮次的最终回复与推理摘要出现朗读按钮，播放器固定在输入区上方；「⚙ 设置」里可关闭。
- 凭据只在桥进程里读取，页面只拿缓存键与音频地址；音频接口只接受 64 位十六进制键，不接受路径。
- 默认关闭（`DSH_BRIDGE_VOICE=0`），未配置或关闭时 `capabilities.voice` 为 `false`，页面不显示按钮。
- 缓存有上限（2 GB 软上限 + 5 GB 磁盘余量）与 30 天清理；同一段文字第二次是缓存命中。

### 进程托管

- 桥进程在端口绑定成功后自己写 `state/bridge.pid`，退出时只删除自己的记录。
- 启动改用 `Win32_Process.Create` 完全脱离父进程，`start-bridge.ps1` 不再挂住不返回。
- 进程身份要求「Python 可执行文件 + 命令行里的绝对 `bridge.py` 路径」，不会误杀同机 Codex 桥或其他 Python。
- 新增 `scripts/tray.ps1`（托盘：状态/打开/启动/停止/重启/退出），只操作自己的 PID。
- `.ps1` 全部改为 UTF-8 BOM，Windows PowerShell 5.1 下中文提示不再乱码。

### 文档

- 新增 `docs/deployment.md`：低侵入的 `/dsh/` 反代写法、Windows 进程托管、上线前检查与回退步骤。
- `docs/api.md` 补充语音与偏好接口；`docs/verification.md` 补充语音实测。
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
