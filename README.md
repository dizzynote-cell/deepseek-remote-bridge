# DeepSeek Remote Bridge

把 **DeepSeek Harness** 的真实会话、工具执行和本机账户余额接到网页上：手机、平板、另一台电脑只负责输入和查看，代码、终端、文件操作仍在你自己的主力电脑上执行。

这是 [Codex Remote Bridge](https://github.com/dizzynote-cell/codex-remote-bridge)（MIT）的**独立适配项目**：复用它的网页外观、Markdown 渲染、附件策略和桥接思路，执行层则按 DeepSeek Harness 自己的官方 SDK 协议重新实现。**不是 DeepSeek 官方产品**，也不修改、不打包 DeepSeek Harness 安装目录。

- 版本：见 [`VERSION`](VERSION)
- 许可：MIT，保留原作者与上游项目归属（见 [`LICENSE`](LICENSE)、[`NOTICE.md`](NOTICE.md)）

---

## 它到底连接了什么

```text
浏览器 / 手机 / 平板
        │  HTTP（可配置基址：/ 或 /dsh/）
        ▼
Python 桥（本项目）—— 会话目录、历史投影、任务状态、余额缓存、附件、交互转发
        │  stdio 双向 JSON-RPC（官方 SDK wire protocol，一行一帧）
        ▼
独立 DSH SDK 运行时进程（dsh --profile sdk --patch <桥生成的补丁>）
        │
        ├─ 真实会话存储（与桌面端同一个 DSH_HOME，可读取既有会话、续写）
        ├─ 真实工具栈（fs / pwsh / 搜索 / 子代理 / 技能 / 沙箱与批准策略）
        └─ 桥自带的宿主适配器插件（loopback HTTP）
              ├─ 原生账户余额（ctx.deepseekAccount.getBalance）
              ├─ 会话列表与完整历史（ctx.sessionQuery）
              └─ 工具批准 / 用户提问（approval/request、user-questions/request）
```

三条边界：

1. **执行层是官方 SDK 协议。** 桥不伪造回合、不拼历史 JSON、不做 UI 自动化。会话所有权由 Harness 自己的跨进程写锁保证：会话正被另一个进程持有（例如桌面端已打开）时，桥返回 `409 session_busy`，**不会**并发改写。
2. **余额是原生账户。** 金额来自 Harness 已登录的 Platform 账户（`/api/v0/users/get_user_summary` 的 `normal_wallets` / `bonus_wallets`），保留十进制字符串与币种，失败就报失败，不显示 0。
3. **宿主适配器只在 127.0.0.1 上监听。** 每次启动随机端口 + 令牌，令牌只落在桥自己的 `state/` 目录；浏览器永远拿不到 Harness 凭据。

## 能力（来自实际实现，不是示例）

| 能力 | 状态 | 说明 |
|---|---|---|
| 读取既有会话 | ✅ | `ctx.sessionQuery` 读真实日志，不改写 |
| 续写既有会话 | ✅ | `session/resume`，被占用时明确报错 |
| 新建会话（真实项目目录） | ✅ | `session/working-directory/set` + `session/prompt` |
| 工具执行 | ✅ | pwsh / bash / read / write / edit / glob / grep / web / skill / subagent |
| 终态判定 | ✅ | `session/wait` + `turn/end.reason`，不把回执当完成 |
| 同会话追加输入 | ✅ | Harness 自身排队（`queuedInput`）；**无** `turn/steer` |
| 取消运行中任务 | ❌ | SDK wire 没有取消方法，桥如实报告 `cancel: false` |
| 工具批准 | ✅ | 桥作为 `approval/request` answerer，网页批准/拒绝 |
| 用户提问 | ✅ | 挂载官方 `dsh-tool-ask-user`，网页回答回填工具调用 |
| 附件（图片/文档） | ✅ | 图片内联为 image block；文档落到桥的 uploads 目录并给出绝对路径 |
| 返回文件 | ✅ | 从工具结果里识别本机文件，网页预览/下载/本机打开 |
| 原生账户余额 | ✅ | 充值钱包与赠送钱包分开显示，多币种不相加 |
| 模型/推理强度切换 | ❌ | 一个运行时固定一条路由；桥不提供假选择器 |
| 语音播报 | ✅（需配置） | 可选：本机 TTS 合成 + 本地缓存，浏览器只拿缓存键与音频地址 |
| 飞书 / 公网云程序 | ❌ | 本阶段不部署服务器；相关配置项保留且默认关闭 |

## 快速开始

### 依赖

- Windows（宿主适配器与运行时的验证环境；桥本身是标准 Python，其他平台未验证）
- Python 3.10+（本机验证：3.13.14；无第三方 Python 依赖）
- 已安装并能正常登录的 DeepSeek Harness 桌面版（本机验证：`0.2.1-rc.2`，SDK `serverInfo` `0.0.1`）
- 浏览器（无构建步骤、无 Node 依赖）

### 配置

```powershell
Copy-Item config\.env.example config\.env
# 编辑 config/.env，至少设置：
#   DSH_EXE=<安装目录>\DeepSeek Harness.exe
#   DSH_CLI=<安装目录>\resources\app.asar\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js
```

不填 `DSH_EXE` 时脚本会探测几个常见安装位置；找不到会打印明确的配置错误，而不是启动一个空桥。

### 启动

```powershell
python bridge.py
# 打开 http://127.0.0.1:8766/
```

常用参数（也可写进 `config/.env`）：

```powershell
python bridge.py --port 8767            # 换端口
python bridge.py --base-path /dsh/      # 挂在反向代理前缀下
python bridge.py --check                # 启动、自检、退出（CI/排障用）
```

`--check` 会真的拉起一次 Harness 运行时并访问适配器，退出码 0 表示运行时与适配器都可用。

### 第一次验证

1. 左侧能看到真实会话列表（按工作目录分组）。
2. 右上角显示「余额 ¥…」与「原生账户 / 更新于 …」，点开有钱包明细。
3. 选一个**测试会话**（或点「新建」），发送一句要求写文件的话，观察工具卡片、终态与历史续写。

## 配置项

全部可选，写在 `config/.env`（优先级：进程环境变量 > `config/.env` > 内置默认值）。

| 变量 | 默认 | 含义 |
|---|---|---|
| `DSH_BRIDGE_HOST` / `DSH_BRIDGE_PORT` | `127.0.0.1` / `8766` | 监听地址与端口（不复用 Codex 的 8765/8780） |
| `DSH_BRIDGE_BASE_PATH` | `/` | 页面基址，支持 `/dsh/` |
| `DSH_EXE` / `DSH_CLI` | 探测 | Harness 可执行文件与 CLI 入口 |
| `DSH_HOME` | 空（用 Harness 默认） | 隔离测试时指向独立数据目录 |
| `DSH_BRIDGE_PROVIDER` / `DSH_BRIDGE_MODEL` | `deepseek-account` / `deepseek-flash` | SDK `initialize` 的模型路由 |
| `DSH_BRIDGE_REASONING_EFFORT` | 空 | 可选推理强度 id（`high`、`max` 等） |
| `DSH_BRIDGE_PERMISSION_PRESET` | `workspace-write` | 新会话权限预设，可选 `danger-full-access` |
| `DSH_BRIDGE_PROJECTS_ROOT` / `DSH_BRIDGE_STANDALONE_DIR` | `~/DSH-Bridge/projects`、`~/DSH-Bridge/sessions` | 允许新建会话的目录白名单 |
| `DSH_BRIDGE_HOME` | `./state` | 桥自己的令牌、生成的补丁、日志、上传目录 |
| `DSH_BRIDGE_ATTACHMENT_MAX_BYTES` / `…_BATCH_MAX_BYTES` | 10 MB / 30 MB | 附件单文件与单批上限 |
| `DSH_BRIDGE_APPROVAL_TIMEOUT_MS` / `…_QUESTION_TIMEOUT_MS` | 15 分钟 | 无人应答时的失败关闭时限 |
| `DSH_BRIDGE_VOICE` | `0` | 打开网页语音朗读（需要 TTS 凭据） |
| `VOLC_TTS_ENV_FILE` | 空 | 指向本机 TTS 凭据文件（`VOLC_API_KEY`、`VOLC_VOICE_ID`、`X-Api-Resource-Id`） |

### 语音（可选）

```ini
DSH_BRIDGE_VOICE=1
VOLC_TTS_ENV_FILE=D:\keys\voiceapi.env
```

- 凭据只在桥进程里读取，页面永远拿不到 key；请求体只有文字。
- 合成结果是 MP3，缓存在 `state/cache/voice/<sha256>.mp3`，同一段文字第二次是缓存命中。
- 页面上打开「⚙ 设置」可以关掉朗读按钮；关闭后服务端也会拒绝语音请求。
- `GET /api/voice/config` 会如实报告 `enabled` / `configured`，任一项为假时不会出现朗读按钮。

## HTTP 接口

所有接口都在配置的基址下（默认 `/`，挂载时 `/dsh/`）。项目自带契约见 [`docs/api.md`](docs/api.md)，与双桥整合相关的两个接口摘要：

```http
GET <base>/api/bridge-info        # 契约版本、providerId、真实能力、LOGO 菜单目标
GET <base>/api/balance            # 原生余额：available/status/source/updatedAt/stale/entries[]
```

`/api/balance` 示例（**示意数据**，不是任何真实账户）：

```json
{
  "providerId": "deepseek-harness",
  "available": true,
  "status": "ok",
  "source": "harness-account",
  "updatedAt": 0,
  "stale": false,
  "entries": [
    {"currency": "CNY", "wallet": "normal", "amount": "0.0000000000000000"},
    {"currency": "CNY", "wallet": "bonus",  "amount": "0"}
  ]
}
```

## 双桥整合预留（给 Codex 的接口）

按双桥整合约定 v1 预留，尚未接入第二个后端：

- **左上角 LOGO 是切换入口**：`#provider-toggle` 打开 `role="menu"` 的菜单，当前项可读名称为「DeepSeek Harness」；Codex 项渲染为 `disabled` + `aria-disabled="true"`，不提供无效跳转。
- **基址可配置**：页面内所有静态资源用相对路径，所有请求经 `web/bridge-client.js` 的 `bridgeFetch` 加前缀；服务端在非 `/` 基址下，前缀之外的路径一律 404，因此不会串到 Codex 的路由。
- **能力与身份**：`/api/bridge-info` 返回 `contractVersion: 1`、`providerId: "deepseek-harness"`、`capabilities`（全部来自实现）、`logoMenu.targets`。
- **状态隔离**：`state/` 下只有桥自己的令牌、补丁、日志、上传；不读也不写 Codex 的数据目录。

详见 [`docs/integration.md`](docs/integration.md) 与 [`docs/deployment.md`](docs/deployment.md)（部署建议与回退）。

## 测试

```powershell
python tests\run_tests.py        # 离线：92 个 Python 用例 + 2 个 Node 页面契约用例
```

真实联调（需要先启动桥；只创建自己的测试会话与测试目录）：

```powershell
python tests\live_check.py       --base http://127.0.0.1:8766            # 列表→历史→新建→工具→终态→重启后续写
python tests\live_interactions.py --base http://127.0.0.1:8766           # 提问与批准闭环
python tests\live_attachments.py --base http://127.0.0.1:8766            # 图片与文档附件
python tests\live_ownership.py   --base http://127.0.0.1:8766 --session <自己的测试会话>
```

本机实测结果与未覆盖项见 [`docs/verification.md`](docs/verification.md)。

## 已知限制

- **没有取消与逐会话关闭**：官方 SDK wire 不提供，桥不会假装有。
- **一个运行时一条模型路由**：换模型需要换运行时，本版不提供模型选择器。
- **同一会话只能被一个进程写**：桌面端已打开的会话会返回 `409`；这是 Harness 的锁，不是桥的缺陷。
- **stdout 噪声**：官方 DeepSeek 账户模型适配器会往 stdout 打一行模型列表诊断（与 SDK 文档「stdout 只放协议帧」冲突），桥按行跳过非 JSON 帧并计数，不影响协议。
- **沙箱依赖宿主环境**：在验证机上 `workspace-write` 预设下 pwsh 子进程以 `3221225794`（DLL 初始化失败）退出；这是宿主沙箱问题，桥如实显示工具结果。可用 `danger-full-access` 预设规避（该预设按 Harness 定义同时关闭批准）。
- **附件只走两条真实通道**：图片内联给模型；其他文件落到桥目录并给出绝对路径让模型读，不做模型侧的“附件解析”。
- **云端与飞书未实现**：本阶段没有部署服务器，也没有第二个飞书应用。

## 隐私

- 不在仓库里放任何真实会话 ID、余额、日志、绝对私有路径、密钥或授权材料。
- `config/.env`、`state/`、`*.pem`、`*.key`、日志与上传目录都在 `.gitignore` 内；发布前另有 `tests/test_public_distribution.py` 扫描实际文件内容。
- 桥只读 Harness 的会话存储来展示历史；写入一律通过 Harness 自己的接口。

## 许可与归属

MIT。上游 [Codex Remote Bridge](https://github.com/dizzynote-cell/codex-remote-bridge)（作者 xiyannan）的网页结构、Markdown 渲染、附件策略与桥接思路被复用并保留署名；DeepSeek Harness 及其运行时、SDK、账户服务均属 DeepSeek，本项目只按官方接口调用，不分发其安装包或用户数据。
