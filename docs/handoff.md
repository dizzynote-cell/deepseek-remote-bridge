# 交接清单（给 Codex 合并服务器与双桥切换）

> 生成时间：2026-10-25 · 交付方：DeepSeek Remote Bridge（DSH 侧）
> 本文件是**当前实现的事实**。与最初 brief 不一致的地方，以本文件为准，原因逐条写在 §3。

## 1. 交付物

| 项 | 值 |
|---|---|
| 仓库 | https://github.com/dizzynote-cell/deepseek-remote-bridge |
| 分支 / HEAD | `main` / `eaf41a8`（以 `git ls-remote` 为准） |
| 版本 | `1.8.0`（`VERSION` 文件；`/api/bridge-info.version` 在进程启动时读取，升级后需重启桥） |
| 许可 | MIT，署名 Codex Remote Bridge 原作者 xiyannan（`LICENSE` / `NOTICE.md`） |
| 依赖 | Python 3.10+，**仅标准库**；无第三方包（`requirements.txt` 为空说明） |
| 启动 | `python bridge.py --port 8766 [--base-path /dsh/]` 或 `scripts\start-bridge.ps1`（后台 + 等健康） |
| 自启 | `scripts\install-shortcuts.ps1 -Desktop -Startup`（隐藏窗口启动 + 打开页面） |
| 自动恢复 | `scripts\install-watchdog.ps1`（计划任务：登录时 + 每 3 分钟健康检查，失败则拉起） |
| 环境模板 | `config/.env.example`（真实 `config/.env` 已被 `.gitignore` 排除） |
| 离线测试 | `python tests\run_tests.py` → **131 个 Python 用例 + 2 个 Node 页面契约用例**，全绿 |
| 真实联调 | `tests/live_*.py` 共 8 个脚本；结果见 `docs/verification.md`（含 11/11 模型切换、13/13 权限、7/7 工具） |
| 界面截图 | `docs/images/desktop.png`、`docs/images/mobile.png`（演示实例，余额为占位） |

运行形态：**Windows 本机常驻服务**，默认只监听 `127.0.0.1:8766`。它不是云服务，也不需要部署到 Linux 服务器。

## 2. 与最初 brief 的对照

brief 要求见 `START_HERE.md`、`INTEGRATION_CONTRACT.md`（本机私有目录，不入库）。

| # | brief 要求 | 现状 | 说明 |
|---|---|---|---|
| 1 | 复用公开桥通用源码，保留 MIT 与归属 | ✅ | 网页结构、Markdown、附件策略、桥接思路复用；执行层按 DSH 官方 SDK 重写 |
| 1 | 真实 DSH 会话：列表→读取→发送→工具→终态→刷新续写 | ✅ | 见 `live_check.py` 10/10、`live_parallel.py` 18/18 |
| 1 | 原生账户余额替换额度 | ✅ | `getBalance` → `/api/balance`，多币种/多钱包不求和，失败不显示 0 |
| 1 | 网页、语音、附件、飞书、云端程序各自实现 | 部分 | 网页/语音/附件 ✅；**飞书未接入**（见 §3.1）；**云端程序未做**（本阶段不部署） |
| 1 | 会话所有权与并发 | ✅ | 被桌面端占用时 `409 session_busy`，不发送（`live_ownership.py` 6/6） |
| 2 | 独立上传 GitHub 公开仓库 | ✅ | 本仓库；无密钥、无会话记录、无本机绝对路径（`tests/test_public_distribution.py` 强制检查） |
| 3 | 交回 Codex 接入服务器 + 左上角 LOGO 切换 | 待 Codex | DSH 侧契约已实现，见 §4、§5 |
| 4 | 右上角显示真实余额 | ✅ | 余额胶囊 + 详情，另加「闲时/忙时」徽标（§3.4） |
| 契约 §2 | 可配置基址，`/` 与 `/dsh/` 都可用 | ✅ | 实测：`/` 与 `/api/*` 在前缀外 404；`/dsh/`、`/dsh/api/*`、`/dsh/sw.js`、`/dsh/manifest.webmanifest` 均 200 |
| 契约 §4 | `GET /api/bridge-info` | ✅ | 含 `contractVersion`、`providerId`、`displayName`、`version`、`basePath`、`logoMenu`、**真实**能力布尔值 |
| 契约 §5 | `GET /api/balance` | ✅ | 含 `providerId`、`available/status/source/updatedAt/stale/entries`，失败保留旧值并标 `stale` |
| 契约 §1 | LOGO 菜单、当前项、禁用无效跳转 | ✅ | 兄弟项由 `DSH_BRIDGE_SIBLING_URL` 驱动；未配置时 `enabled:false, reason:"not-configured"` |
| 契约 §1 | 切换不迁移会话/草稿、不取消任务、不重启进程 | ✅（本侧） | 整页跳转，各自保存；DSH 侧不提供任何跨桥迁移接口 |
| 契约 §1 | 保存最后 provider / 各端上次会话 | 入口页职责 | DSH 用 `?thread=` 深链，刷新/返回可恢复；"最后 provider"需由入口页存 |
| 契约 §1 | 另一边离线时菜单标不可达 | 入口页职责 | 本侧只给出 `enabled/url`，不做可达性探测（避免误导） |
| 契约 §3 | 前端选择不是授权、请求绑定 provider | 待 Codex | DSH 侧无认证，**必须**由入口/反代补鉴权，见 §5.3 |

## 3. 与原计划的偏差（按现状执行）

### 3.1 飞书：不接入，只留安装与登录施工图

用户明确要求"先不接飞书，以后要接就用飞书鉴权登录"。因此本仓库**没有**飞书机器人、没有长连接、没有 `FEISHU_*` 运行时逻辑；只提供：

- `docs/feishu.md`：为什么本阶段不接、阻塞点；
- `docs/feishu-setup.md`：飞书 OAuth 应用创建、回调、scope、绑定与验收步骤。

合并时若要做飞书，按契约 §3：**只能有一个入口消费消息**，入口按已保存绑定路由到某台机器人的桥；不要让两边同时连同一个应用。

### 3.2 服务器与云端：本阶段刻意不做

- 没有 `cloud/`、没有云端数据库、没有 systemd/Caddy 配置、没有域名/IP 写死；
- 不允许把 DSH runtime 或模型凭据搬到服务器（契约 §2 末段）。

### 3.3 取消 / steer / 中途插话

官方 SDK 协议没有 `cancel`、没有按回合 steer、没有"关闭会话"。能力位如实为 `false`；排队输入（同一会话再发一条）由 Harness 自身处理，桥不伪造。

### 3.4 DSH 侧新增（brief 未要求，但已实现）

| 功能 | 说明 |
|---|---|
| 会话内权限切换 | `workspace-write` / `danger-full-access`，写回 Harness 日志；未知值 400，占用 409 |
| 模型与推理强度 | `V4.1-Flash` / `V4-Pro` × `off/low/high/max`；切换=停旧运行时→起新运行时→resume 同一会话（历史保留，日志出现两个 `request/header`） |
| 计划与实时状态 | `todo/write` → 计划卡 x/y；实时条显示轮次/当前工具/工具数/用时 |
| 闲时/忙时徽标 | 只报时段与倒计时，不报价格、不参与计费；时段可配 `DSH_BRIDGE_PEAK_*` |
| 托盘 + 快捷方式 + 看门狗 | 见 §1；托盘只管理 `state/bridge.pid` 记录的进程 |
| 工具补齐 | `present`、`load_workspace_dependencies` 已挂载；模型可见 **28/32** |

### 3.5 工具集缺口（明确已知）

桌面 profile 32 个工具，桥 28 个；缺 `schedule_create/delete/list/update`。原因：`tool-schedule` 依赖 `dsh-schedule` 与桌面 `sessionController`，SDK 组合没有，补丁层无解。其余 28 个逐个实测可调用（`live_tools.py` 7/7）。

## 4. 给 Codex 的合并步骤（服务器侧）

### 4.1 拓扑（必须先确认）

DSH 桥**跑在用户的 Windows 电脑上**（因为要驱动本机 Harness 会话与工具）。服务器不能运行它，只能做入口与反代：

```
浏览器 ──HTTPS──> 你的服务器（Codex 入口页，/）
                      │  反代 /dsh/  ──> 隧道（frp/ssh -R/Cloudflare Tunnel）
                      │                      │
                      └──────────────────────┴──> 用户电脑 127.0.0.1:8766（DSH 桥）
```

两种可用形态，二选一：

- **A（契约首选）**：入口保持 `/`，DSH 挂 `/dsh/`，反代到隧道。需要 DSH 侧设 `DSH_BRIDGE_BASE_PATH=/dsh/`。
- **B**：DSH 独立主机名（如 `dsh.example.com`），各自独立认证；此时 DSH 基址为 `/`，菜单里 `DSH_BRIDGE_SIBLING_URL` 指向入口页。

### 4.2 DSH 侧配置（用户电脑）

```ini
# config/.env
DSH_BRIDGE_HOST=127.0.0.1
DSH_BRIDGE_PORT=8766
DSH_BRIDGE_BASE_PATH=/dsh/                      # 形态 A；形态 B 留 /
DSH_BRIDGE_SIBLING_PROVIDER=codex
DSH_BRIDGE_SIBLING_NAME=Codex
DSH_BRIDGE_SIBLING_URL=https://<入口页域名>/     # 只接受 http(s) 绝对地址
```

改完重启桥（`scripts\restart-bridge.ps1`）。升级代码后也必须重启，`/api/bridge-info.version` 才会更新。

### 4.3 入口页（Codex）要做的

1. 左上角 LOGO 菜单增加 `DeepSeek Harness` 项，跳 `/dsh/`；`Codex` 项跳回 `/`。
2. 读 `GET /dsh/api/bridge-info` 的 `logoMenu.targets`（DSH 侧也可作为数据源）；
   本侧未配置 `SIBLING_URL` 时该项是 `enabled:false`，入口页应自己渲染可点项，不要依赖 DSH 侧跳转。
3. 保存"最后使用的 provider"和各自上次会话（DSH 用 `?thread=<sessionId>` 深链）。
4. 切换到不可达的一侧时显示"离线/不可达"，**不要**重试重启任何进程。
5. 鉴权：在服务器侧完成（会话/票据/Basic Auth 均可），**不要**把 `X-Provider` 当授权。

### 4.4 反向代理要点

- 必须转发：`Host`、`X-Forwarded-*`、`Accept`、`Content-Type`、请求体（附件上传最大 10 MB/单文件、30 MB/批）；
- 必须支持 **SSE 不需要**（本桥没有事件流），但**长请求**要放开超时：`/api/local-tasks` 最长 120 s 才返回 `taskId`，会话运行本身走轮询；
- 不要缓存 `/api/*`（静态资源可缓存，带 `?v=` 或哈希）；`sw.js` 必须允许更新（同源 scope `/dsh/`）；
- 路径：`/dsh` 建议 301 到 `/dsh/`；前缀之外必须 404，本桥已自带该行为（实测）。
- WebSocket：本桥不使用；若入口页有 WS，注意不要把它反代到 `/dsh/`。

### 4.5 隔离检查（合并后逐条验证）

```powershell
# 在服务器上（把 host 换成你的域名）
curl -i https://<host>/                      # 200（Codex）
curl -i https://<host>/dsh/                  # 200（DSH）
curl -i https://<host>/dsh/api/bridge-info   # 200，providerId=deepseek-harness
curl -i https://<host>/api/bridge-info       # 404（不能串到 DSH）
curl -i https://<host>/dsh/sw.js             # 200，Content-Type: text/javascript
```

Cookie 不得同名跨用；两边 `state/`、`uploads/`、语音缓存、日志各自独立；本桥不写任何 Cookie。

## 5. 契约接口（DSH 实际实现）

所有接口都在基址下（`/dsh/api/...`），只接受同源写请求（`Origin`/`Sec-Fetch-Site` 校验），无鉴权（由入口负责）。

| 接口 | 说明 |
|---|---|
| `GET /api/bridge-info` | 元信息 + `logoMenu.targets` + 真实能力位（见 §2） |
| `GET /api/balance[?refresh=1]` | 原生余额；失败 `available:false` + `status`，旧值保留并 `stale:true`；附带 `pricing` 时段对象 |
| `GET /api/models[?threadId=]` | 模型/强度表、新会话默认、本会话当前路由 |
| `POST /api/models` | `{model,effort}` 改默认；`{threadId,model,effort}` 切换本会话（409 `session_running`/`route_busy`） |
| `GET/POST /api/permissions` | 权限预设读取/切换；未知 400，占用 409 |
| `GET /api/threads` / `GET /api/thread/<id>?turnLimit=` | 会话列表 / 转录（含 `bridgeTodos`、`bridgeActivity`、`bridgeModel`） |
| `POST /api/local-tasks` | `op:new_thread`（`cwd,title,text,permissionPreset,model,effort`）或 `{threadId,text}` 继续；返回 `taskId` |
| `GET /api/local-task/<id>` | 任务终态：`completed/failed/interrupted/cancelled` |
| `GET /api/interactions` / `POST /api/interactions/respond` | 批准与提问；应答绑定 `session + request id` |
| `POST /api/attachment` / `GET /api/open-attachment` | 附件上传（图片内联、文档落盘）/ 主机打开（白名单） |

错误码约定：`400`（参数/未知模型/未知预设）、`409`（会话被占用/正在运行）、`502`（适配器或运行时失败）、`404`（前缀外/未知会话）。

## 6. 合并验收清单（对应契约 §6）

| # | 验收项 | 期望 | DSH 侧依据 |
|---|---|---|---|
| 1 | `/` 与 `/dsh/` 资源、接口、下载、音频互不串 | `/` 404 on DSH；`/dsh/api/*` 200；静态资源相对路径 | 本文件 §4.5 实测 |
| 2 | 双向切换、刷新、手机返回、恢复上次会话；草稿不跨端发送 | 整页跳转；DSH `?thread=` 恢复；无跨端发送接口 | 实现即如此 |
| 3 | 两边任务并行，切换/离线不影响已有任务 | DSH 任务在桥进程内运行，页面刷新不取消 | `live_parallel.py` 18/18 |
| 4 | 匿名/伪造 provider/跨端 taskId/错误令牌不能互访 | 入口鉴权 + 前缀隔离；DSH 不认任何 provider 头 | 本桥无 provider 信任逻辑 |
| 5 | Codex 额度与 DSH 余额分别更新；失败/未登录准确显示 | `available:false` + `status`，不显示 0 | `test_balance.py` 7 个用例 |
| 6 | 语音缓存/附件/上传/停止脚本只作用于各自目录与进程 | 全部落在 DSH `state/`；按 pid 文件停进程 | `tests/test_public_distribution.py`、`live_files.py` |
| 7 | 飞书只消费一次（若启用） | 单入口长连接 + 绑定路由 | 本阶段未接（§3.1） |
| 8 | 有回退方案，重启取得当次授权 | 回退步骤见 §7 | — |

## 7. 回退方案

1. 入口页把 `DeepSeek Harness` 菜单项去掉或置灰（DSH 侧不需要改）；
2. 服务器反代删掉 `/dsh/` location（不影响 Codex 原路径）；
3. 用户电脑上 `scripts\stop-bridge.ps1` 停桥；如装了看门狗，先 `scripts\install-watchdog.ps1 -Remove`；
4. 需要彻底移除时：`scripts\install-shortcuts.ps1 -Remove` 删快捷方式与登录项，再删仓库目录；
5. 回退不涉及 Codex 仓库、Codex 进程或 DSH 安装目录。

## 8. 已知限制（交付即生效）

- 无任何登录/鉴权；公网暴露前必须由入口或反代加认证。
- 只验证 Windows 10 22H2 + DSH `0.2.1-rc.2`；语音只实测火山 TTS。
- 无取消、无按回合 steer、无模型热切换（切换会重启该会话的运行时，几秒）。
- `schedule_*` 4 个工具不可用（§3.5）。
- 桌面 profile 的自定义 patch（如 MCP 服务器）不会继承到桥；需要时写进桥的 `--patch`。
- 桌面端正在打开的会话被写锁占用，桥返回 `409`，不抢。
- 余额缓存 60 秒；`?refresh=1` 强制刷新。

## 9. 私有材料（不进仓库）

本机私有 brief 目录（`START_HERE.md`、`INTEGRATION_CONTRACT.md` 等）、`config/.env`、`state/`、真实会话 ID/余额/日志、TTS 凭据文件。公开仓库已用测试强制扫描（私有路径、token、会话 ID、私钥模式）。
