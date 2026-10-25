# 更新日志

## 1.8.0 — 2026-10-25

输入区改成两行：上面是模型/强度/权限，下面是输入框。

- 新增 `#control-bar`：模型、强度、权限三个可点按钮独占一行，输入框那一行只剩 ＋ / 输入 / 发送，
  文本框不再被挤窄（桌面 764px，手机 248px 且操作区不换行）。
- 三个菜单统一成同一套组件与样式（深色卡片、标题+说明两行、当前项高亮），不再出现
  “样式像浏览器原生下拉”的权限选择器。
- 权限菜单改成中文名（工作区写入 / 完全访问）+ 中文说明；原来的原生 `<select>` 从输入区移除，
  设置对话框里的“新会话默认权限”仍是表单下拉。
- UI 契约测试加了断言：必须有 `#control-bar`、`#effort-chip`、`#permission-chip`，
  且不能再出现原生 `<select id="permission-select">`。
- README 的两张界面截图重新拍过（含新的设置行、计划卡 3/4、闲时徽标）。

## 1.7.1 — 2026-10-25

桥挂了能自己回来。

- 新增 `scripts/install-watchdog.ps1`：注册计划任务（登录时 + 每 3 分钟），调用
  `scripts/watchdog-once.ps1` 做一次健康检查；不健康或进程不在就启动桥和托盘。
- 新增 `scripts/watchdog-once.ps1`（一次性检查版，适合计划任务；`watchdog.ps1` 仍是常驻循环版）。
- `install.ps1` 默认询问是否注册该任务；README 补了「挂了会自动回来」一节，并说明主动维护前要先移除任务。
- 实测：停掉桥 → 手动触发任务 → 5 秒内恢复，`LastTaskResult=0`，页面 HTTP 200。

## 1.7.0 — 2026-10-25

输入框左侧多了「模型」：可以选 V4.1-Flash / V4-Pro，以及四档推理强度。

- `GET /api/models` 返回模型表、强度表、新会话默认值和（传入 `threadId` 时）本会话路由；
  `POST /api/models` 不带 `threadId` 改新会话默认，带 `threadId` 切换本会话。
- 切换是**真的**：SDK 的模型固定在运行时进程上，桥会停掉该会话原来的运行时，用新模型重启，
  再把同一个会话 `session/resume` 过去；实测同一份会话日志先后出现
  `deepseek-account/deepseek-flash` 与 `deepseek-account/deepseek-v4-pro` 两个 `request/header`。
- 保护：本会话在跑 → `409 session_running`；同模型上还有别的会话在跑 → `409 route_busy`；
  未知模型/强度 → `400`。会话路由持久化到 `state/data/session-routes.json`，桥重启不会把 pro 会话
  悄悄退回默认模型。
- 页面上是一个「V4.1 Flash · 最大」样式的按钮，点开分组选择；没有选中会话时改的是新会话默认值。
- 测试：新增 `tests/test_models.py`（7 个用例）与 4 个 HTTP 用例；新增 `tests/live_models.py`（11/11，
  真实切换 + 第二轮在新模型上完成 + 未知模型拒绝 + 切回原路由）；合计 131 个 Python 用例。

## 1.6.2 — 2026-10-25

README 换成真实界面截图。

- 新增 `docs/images/desktop.png`（1440×900，含左侧项目/会话列表、计划卡与回复）与更新后的
  `docs/images/mobile.png`（390×844 @2x，含时段徽标、计划卡与两行输入区）。
- 两张图都来自隔离的演示实例：独立的 `DSH_HOME` 与演示项目目录，因此不会出现本机真实会话标题；
  余额在截图前替换为占位数字。
- 删除已过期的 `conversation.png`（旧 Logo、旧余额文案）。

## 1.6.1 — 2026-10-25

换成自己的鲸鱼 Logo。

- 页面左上角的方形标记从"字母 D"换成图片（`web/logo-64.png`）。
- 同一套图生成 PWA 图标：`icon-192.png`、`icon-512.png`、`icon-maskable-512.png`（蓝底安全区）、
  `apple-touch-icon.png`（蓝底，避免 iOS 透明处变黑）、`favicon-32.png`。
- 托盘不再画字母：优先加载 `assets/bridge.ico`（16/32/48/64/256 多尺寸），缺失时才回退到绘制；
  回退色也从绿色改成 DeepSeek 蓝。
- 桌面快捷方式与开机启动项指向同一个 `.ico`，重建后已刷新图标缓存。
- `/favicon.ico` 与 `/apple-touch-icon.png` 不再固定回退到 SVG：前者优先送 `favicon-32.png`，后者直接送 PNG。
- 母版图存为 `assets/logo.png`，方便以后重新生成各尺寸。

## 1.6.0 — 2026-10-25

余额旁边多了"闲时/忙时"。

- `GET /api/balance` 增加 `pricing`：当前时段、本地时间、时区、规则说明、下次切换时间与剩余秒数。
- 页面在余额胶囊里显示一个时段徽标（闲时蓝、忙时琥珀），点开余额详情能看到
  "周日 2026-10-25 04:22 · UTC+08:00 / 28 小时 37 分后转为忙时（2026-10-26 09:00）"与规则说明。
- 时段不写死：`DSH_BRIDGE_PEAK_WINDOWS`、`DSH_BRIDGE_PEAK_DAYS`、`DSH_BRIDGE_PEAK_OFFSET_MINUTES`
  可改；配置写坏时回退默认值并在 `invalid` 里说明，不静默瞎算。
- 桥只报时段，不报价格、不参与计费；金额仍然只来自 Platform 原生余额。
- 新增 `tests/test_pricing.py`（13 个用例：边界、周末、跨天、下一次切换、非法配置），合计 120 个 Python 用例。

## 1.5.0 — 2026-10-25

为合并进 Codex 工作台做准备：桌面/开机快捷方式，以及可配置的左上角切换目标。

- `scripts/install-shortcuts.ps1`：建/删桌面快捷方式与登录启动项，统一指向
  `scripts/launch-bridge.ps1`（隐藏窗口启动桥 + 托盘，再打开页面；已在运行就只开页面）。
- 新增 `assets/bridge.ico`（16/32/48/64/256 多尺寸）作为快捷方式图标；安装向导默认两者都建，
  `install-autostart.ps1` 保留为只建启动项的兼容入口。
- 左上角菜单的兄弟后端可配置：`DSH_BRIDGE_SIBLING_PROVIDER/NAME/URL`。
  配置合法 `http(s)` 地址后该项变为可点跳转；空值或非法地址保持禁用（不出死链）。
  菜单数据仍由 `/api/bridge-info` 的 `logoMenu.targets` 提供，入口页可自行渲染。
- 新增 `tests/test_logo_menu.py`（5 个用例）覆盖启用/禁用与地址校验；合计 107 个 Python 用例。

## 1.4.1 — 2026-10-24

让桥不依赖桌面窗口也能拿到与桌面端一致的工具集。

- 补丁新增官方 `@deepseek-ai/dsh-tool-present`；`load_workspace_dependencies` 通过自动探测
  `DSH_PRIMARY_RUNTIME`（`<安装目录>/resources/runtime/primary-runtime`）激活。
- 实测模型可见工具：26 → **28**（桌面 32）。仍缺 `schedule_create/delete/list/update`：
  它们需要 `dsh-schedule` 与桌面会话控制器，SDK 组合不含，补丁层面无解。
- README 增加「不开桌面客户端能用吗」，说明桥自己启动运行时、凭据来源、以及仍需桌面的三件事
  （重新登录、桌面 profile 的自定义 patch、被桌面打开的会话锁）。
- 新增 `tests/live_tools.py`（7/7），把工具可用性做成可重跑的检查。

## 1.4.0 — 2026-10-24

补上「做到哪了、还剩几步、能不能换权限」三件事。

### 执行计划与实时状态

- 投影 `todo/write`：计划清单 + 完成度，进入历史（同一轮只保留最新一版，不刷屏），表头显示「计划 x/y」。
- 实时状态条：当前轮次、正在运行的工具、工具调用次数、计划进度、已用时；只由 `session.event` 推导，不编造。
- 会话之间互不影响：切到别的会话不会取消运行中的任务。

### 会话内权限切换

- `GET/POST /api/permissions`：读取运行时预设目录（`workspace-write` / `danger-full-access`），
  会话内切换写回 Harness（`permission/preset` + `sandbox/mode`），未知预设 400，被占用 409。
- 输入框左侧新增「权限」选择器；「设置」里可设新会话默认权限（存桥的 preferences，创建时应用）。
- 新会话在**第一条消息前**应用预设，避免首轮工具调用用错权限。

### 文档与测试

- `docs/api.md` 补权限接口与 `bridgeTodos` / `bridgeActivity` 字段；`docs/roadmap.md` 更新已完成项。
- 新增 `tests/live_permissions.py`（13/13）；新增计划投影、卡片去重与权限接口测试，合计 102 个 Python 用例。

## 1.3.0 — 2026-10-24

换成 DeepSeek 蓝主题，补上手机安装与两份施工图文档。

### 界面

- 全站配色从上游的绿色改为 DeepSeek 蓝：主色 `#4d6bfe`、深色渐变 `#2f49c9`、面板与文字一起蓝移；
  侧栏 logo、链接、按钮、用户气泡、工具卡片、推理卡、语音按钮、余额药丸统一到同一套变量。
- 余额按钮补上边框/背景与展开态，不再吃浏览器默认灰色按钮样式。
- 新增应用图标（favicon.svg、icon-192/512、apple-touch-icon）与运行时拖盘图标同源配色。

### 手机安装（PWA）

- `manifest.webmanifest`（`display: standalone`、主题色、图标）、`theme-color`、iOS 元信息。
- `sw.js` 只预缓存静态资源；`/api/` 与页面导航永不缓存，避免看到过期的桥状态。
- Service Worker 仅在安全上下文注册，局域网 http 打开时静默退化。
- 服务端为 `.webmanifest` / `.svg` 补正确 MIME。

### 文档

- 新增 `docs/roadmap.md`：对照官方 SDK 的 7 个方法/4 个通知逐项列出已用、未用、受限于 SDK 的能力，
  以及手机场景的缺口与 P0/P1/P2 优先级。
- 新增 `docs/feishu-setup.md`：飞书 OAuth 登录的安装引导（应用创建、重定向 URL、最小权限、state 校验、
  cookie 设计、白名单、限流、验收清单），并说明为什么不用自建账号密码。
- `docs/feishu.md` 保留为"为什么现在不接"的阻塞说明。

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
