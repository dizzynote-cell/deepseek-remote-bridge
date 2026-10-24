# 验证记录

记录日期：2026-10-24。所有项目都在**本机**运行，使用桥自己创建的测试会话与测试目录；没有向既有用户会话发送探测输入。

## 环境

| 项 | 值 |
|---|---|
| 系统 | Windows（x64） |
| Python | 3.13.14（仅标准库） |
| DeepSeek Harness | `0.2.1-rc.2`（安装包 `app.asar`） |
| SDK 运行时 `serverInfo` | `deepseek-harness-sdk-runtime` / `0.0.1` |
| 模型路由 | provider `deepseek-account`，model `deepseek-flash` |
| 浏览器验收 | Chrome `headless=new`，1440×900 与 390×844 |

## 离线测试

```text
python tests\run_tests.py
```

覆盖：

| 模块 | 用例 | 验证点 |
|---|---|---|
| `test_history_view.py` | 9 | 用户/助手/推理/工具/通知投影、Harness 上下文不冒充用户、turn 终态、usage 聚合、有效工作目录、消息 ID |
| `test_balance.py` | 7 | 十进制字符串保留、多币种不相加、失败不归零、过期快照、缓存、非法金额丢弃 |
| `test_config_and_patch.py` | 11 | 基址归一化、环境变量优先级、配置缺项、归档内 CLI、令牌持久化、补丁内容与注入防护 |
| `test_attachments.py` | 10 | 文档落盘、图片内联、单文件/单批限制、非法 base64、路径逃逸、清理、返回文件白名单 |
| `test_interactions.py` | 9 | 批准/提问整形、accept→`allowed-once`、拒绝、选项与自定义回答、空回答拒绝、revision、适配器失败 |
| `test_tasks.py` | 9 | 占用→409 且不重发、发送失败不重试、resume 一次、终态映射、消息 ID 定位 turn、未完成标中断 |
| `test_server.py` | 15 | 根/前缀挂载、前缀外 404、302、运行中标红、turn 裁剪、跨站写拒绝、目录穿越、CSRF、语音与偏好接口 |
| `test_voice.py` | 18 | 与页面一致的缓存键、配置解析、未配置/关闭时拒绝、空文本与超长、缓存命中、非法键、缓存目录边界、偏好持久化 |
| `test_public_distribution.py` | 7 | 私有路径/密钥/会话 ID 扫描、二进制排除、必需文件、`.gitignore`、许可与归属 |
| `test_markdown.cjs` | 1 | 表格、代码、流式片段、危险链接与原始 HTML 转义 |
| `test_ui_contract.cjs` | 1 | 资源存在、全部请求走基址、余额替代额度、LOGO 菜单与禁用项、语音按钮与密钥不外泄 |

合计：95 个 Python 用例 + 2 个 Node 用例（含 PWA 与配色检查）。

## 真实联调

| 脚本 | 结果 | 实际验证内容 |
|---|---|---|
| `live_check.py --phase create` | 10/10 | 新建真实会话、`write` 工具真的落盘、历史含用户消息与工具调用、turn `completed` |
| `live_check.py --phase resume` | 5/5 | 桥重启后同一会话仍可读、可 `resume`、第二轮完成、历史 1→2 轮 |
| `live_interactions.py` | 11/11 | `ask_user_question` 到桥、回答回填、模型按所选值写文件；沙箱升级批准到桥、批准后工具真的执行；批准审计事件出现在历史 |
| `live_attachments.py` | 6/6 | 图片作为 image block 被模型理解（回答 green）、文本文档被模型读取（回答 magic 值）、上传落在桥目录 |
| `live_parallel.py` | 18/18 | 两个会话同时运行且都出现在运行列表中、各自落盘、各自完成；空闲会话的后续输入 `steered:false`；运行中的第二个输入 `steered:true` 且两条都完成 |
| `live_files.py` | 6/6 | 生成的 `.md` 与 `.csv` 作为返回文件出现、下载 200 且类型正确、白名单外的路径 403、白名单外的目录 403 |
| `live_ownership.py` | 6/6 | 第二个 Harness 运行时持有会话时桥返回 `409 session_busy`、未写入内容、历史仍可读、对方退出后恢复 |

## 基址与静态资源

| 请求（`--base-path /dsh/`） | 结果 |
|---|---|
| `GET /dsh/` | 200，注入 `window.DSH_BRIDGE.basePath="/dsh/"` |
| `GET /dsh` | 302 → `/dsh/` |
| `GET /dsh/app.js` | 200 |
| `GET /dsh/api/health` | 200 |
| `GET /dsh/api/threads` | 200 |
| `GET /api/health`、`GET /api/threads`、`GET /app.js`、`GET /` | 404（前缀外不响应） |

页面 HTML 中所有 `src`/`href` 均为相对路径，没有绝对 `/...` 资源。

## 浏览器验收

- 1440×900：侧栏按项目分组列出真实会话，右上角显示真实余额与「原生账户 / 更新于 …」，状态条显示当前路由与权限预设。
- `?thread=<id>` 深链：直接打开会话，用户消息、附件提示、`read`/`read_image` 工具卡片、推理折叠卡、用量与最终回答都正确渲染。
- 左上角 LOGO 菜单：当前项 DeepSeek Harness 高亮，Codex 项置灰禁用（`aria-disabled="true"`），无 href。
- 余额面板：充值钱包与赠送钱包分行、币种、金额、来源、更新时间与「刷新余额」按钮。
- 390×844（CDP 设备模拟，DPR 2）：自动切到紧凑抽屉布局（`mobile-ui` 由视口宽度与 UA 共同决定），`scrollWidth == clientWidth == 390`，没有横向溢出；侧栏可收起，输入区可用。

## 语音（1.1.0 新增，实测）

| 检查 | 结果 |
|---|---|
| `GET /api/voice/config` | `configured:true`，音色与 resource 来自本机凭据文件 |
| `POST /api/voice/request`（中文一句） | 789 ms，返回 64 位键，MP3 10,605 字节 |
| 同文本第二次请求 | 12 ms，键相同（缓存命中，未再调用供应商） |
| `GET /api/voice/audio/<key>` | 200，`audio/mpeg`，首字节 `49 44 33`（ID3） |
| 浏览器解码 | `AudioContext.decodeAudioData` → 1.32 s，48 kHz，单声道（音频有效） |
| `GET /api/voice/audio/<非法键>` | 404，不读任何路径 |
| 页面 | 完成轮次的最终回复出现朗读按钮；点击后底部播放器出现；「⚙ 设置」显示 TTS 已配置并可关闭 |
| 能力 | `capabilities.voice` 在 `DSH_BRIDGE_VOICE=1` 且凭据齐全时为 `true`，否则 `false` |

## 发布物验证

从 GitHub 全新 clone（`0fd44ba…`），只补一个未跟踪的 `config/.env`，然后：

1. 不写配置直接启动 → 退出码 2，提示设置 `DSH_EXE` / `DSH_CLI`（错误路径可用）。
2. 补配置后启动 → `/api/health` `ok:true`，运行时与适配器均可用。
3. `python tests\run_tests.py` → 当时 70 个 Python 用例 + 2 个 Node 用例全部通过（1.3.0 为 95 + 2）。
4. `python tests\live_check.py --phase create` → 10/10，真实会话与真实工具落盘。
5. `/api/bridge-info` 的 `capabilities.voice` 与实际实现一致（未配置时为 `false`）。

## 未覆盖 / 未实现

| 项 | 状态 |
|---|---|
| 取消运行中的任务 | 未实现：官方 SDK wire 没有取消方法；`capabilities.cancel=false` |
| 同会话 steer 指定回合 | 未实现：只有 Harness 自身的排队输入 |
| 模型 / 推理强度切换 | 未实现：一个运行时固定一条路由，页面不提供假选择器 |
| 语音供应商 | 只实测了火山 TTS（`seed-tts-2.0`）；其他供应商未接 |
| 飞书 | 未实现：需要独立应用与长连接，本阶段不做 |
| 云端程序与部署 | 未实现：未部署、未访问服务器 |
| 非 Windows 宿主 | 未验证：适配器与运行时只在 Windows 上跑过 |
| 超长历史的翻页性能 | 未压测：历史按需读取整份日志，超大日志会变慢 |
| 多用户 / 远程暴露 | 未验证：默认只监听回环，没有用户体系 |

## 宿主环境注意事项

在本次验证机上，`workspace-write` 预设下 pwsh 子进程以 `3221225794`（`0xC0000142`，DLL 初始化失败）退出：这是宿主沙箱与该 shell 组合的问题，不是桥的判定问题——桥如实显示 `[exit code: 3221225794]`，模型也会看到。切到 `danger-full-access` 预设后命令正常执行（该预设按 Harness 定义同时把批准策略设为 `never`）。

另外，官方 DeepSeek 账户模型适配器会向 **stdout** 打一行模型列表诊断，而 SDK 文档要求 stdout 只放协议帧。桥按行跳过非 JSON 帧并计数，协议不受影响；这是一条已记录的上游行为。
