# HTTP 接口

基址由 `DSH_BRIDGE_BASE_PATH` 决定（默认 `/`，可挂 `/dsh/`）。下文 `<base>` 指该前缀。
所有响应都是 `application/json; charset=utf-8`，错误体为 `{"error": "...", "code": "..."}`。

## 页面与静态资源

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `<base>/` | 页面（注入 `window.DSH_BRIDGE` 基址与品牌） |
| GET | `<base>/<file>` | 静态资源；拒绝 `..` 与点文件 |
| GET | `<base>` | 302 到 `<base>/` |

前缀之外的路径一律 `404 unknown_path`，因此同源挂载时不会串到 Codex 桥。

## 状态与元信息

```http
GET <base>/api/health
```

```json
{
  "ok": true,
  "version": "1.0.0",
  "uptimeSeconds": 12.3,
  "runtime": {"routes": [{"route": "deepseek-account/deepseek-flash", "alive": true,
                          "sessions": 1, "adapterPort": 51423,
                          "serverInfo": {"name": "deepseek-harness-sdk-runtime", "version": "0.0.1"}}],
              "lastError": "", "bootError": ""},
  "adapter": {"ok": true, "error": ""},
  "lastSessionsError": "",
  "staging": {"dshExe": "...", "dshExeExists": true, "dshCli": "...", "dshCliExists": true,
              "dshHome": null, "adapterFile": "...", "adapterFileExists": true, "generatedAt": 0}
}
```

```http
GET <base>/api/bridge-info
```

```json
{
  "contractVersion": 1,
  "providerId": "deepseek-harness",
  "displayName": "DeepSeek Harness",
  "version": "1.0.0",
  "capabilities": {"existingSessions": true, "newSession": true, "steer": false,
                   "queuedInput": true, "cancel": false, "attachments": true,
                   "approvals": true, "userQuestions": true, "voice": false,
                   "billing": "balance", "modelSelection": false},
  "runtime": {"provider": "deepseek-account", "model": "deepseek-flash",
              "reasoningEffort": null, "permissionPreset": "workspace-write"},
  "basePath": "/",
  "logoMenu": {"enabled": true, "current": "deepseek-harness",
               "targets": [{"providerId": "deepseek-harness", "displayName": "DeepSeek Harness",
                            "current": true, "enabled": true},
                           {"providerId": "codex", "displayName": "Codex", "current": false,
                            "enabled": false, "reason": "not-configured", "url": null}]}
}
```

```http
GET <base>/api/status
```

`{"label": "真实 DSH 会话 · deepseek-account/deepseek-flash · 预设 workspace-write", "shortLabel": "真实 DSH 会话 · deepseek-flash", "mode": "local", "runtime": {...}}`

## 会话

```http
GET <base>/api/threads?limit=50&cursor=<offset>&project=<cwd>
```

```json
{
  "threads": [{"id": "session-<id>", "name": "会话标题", "status": "空闲",
               "cwd": "D:\\work\\demo", "bridgeCreatedAt": 0, "bridgeUpdatedAt": 0,
               "bridgeLive": false, "bridgeAgentPreset": "standard", "bridgeRunning": false}],
  "projects": [{"cwd": "D:\\work\\demo", "name": "demo", "count": 1}],
  "nextCursor": "50"
}
```

`bridgeRunning` 来自桥自己的任务表：有任务在跑时 `status` 为 `运行中`。

```http
GET <base>/api/thread/<sessionId>?turnLimit=6
```

```json
{
  "thread": {
    "id": "session-<id>", "name": "...", "cwd": "...", "status": "idle",
    "turns": [{"id": "turn-1", "status": "completed", "bridgeTurn": 1,
               "bridgeModel": {"provider": "deepseek-account", "model": "deepseek-flash"},
               "items": [
                 {"type": "userMessage", "content": [{"type": "text", "text": "..."}],
                  "bridgeMessageId": "<id>", "bridgeImageCount": 0, "bridgeSource": "user"},
                 {"type": "reasoning", "text": "..."},
                 {"type": "agentMessage", "text": "...", "phase": "final"},
                 {"type": "toolCall", "name": "write", "callId": "...", "arguments": "...",
                  "result": "...", "status": "completed", "isError": false},
                 {"type": "todo", "todos": [{"content": "读取需求", "status": "completed"}]},
                 {"type": "notice", "level": "approval", "text": "请求批准：pwsh — ..."},
                 {"type": "usage", "inputTokens": 0, "outputTokens": 0, "totalTokens": 0}
               ]}],
    "bridgeTotalTurns": 3, "bridgeProvider": "deepseek-account", "bridgeModel": "deepseek-flash",
    "bridgeUpdatedAt": 0, "bridgeEventCount": 42, "bridgeAttachments": [],
    "bridgeTodos": [{"content": "读取需求", "status": "completed"}, {"content": "写文件", "status": "in_progress"}],
    "bridgeTodosDone": 1, "bridgeTodosTotal": 2,
    "bridgeActivity": {"running": true, "turn": 2, "turnId": "turn-2", "startedAt": 0, "endedAt": 0,
                       "tools": 3, "currentTool": "write", "todosDone": 1, "todosTotal": 2}
  }
}
```

`turnLimit` 只裁剪返回的最近若干轮，`bridgeTotalTurns` 始终是真实总数（页面据此翻更早的轮次）。
`404 session_not_found`、`502 adapter_error` 分别表示会话不存在与适配器读取失败。

## 发送

```http
POST <base>/api/local-tasks
Content-Type: application/json
```

续写既有会话：

```json
{"threadId": "session-<id>", "text": "继续", "files": []}
```

新建会话（第一轮）：

```json
{"op": "new_thread", "cwd": "D:\\work\\demo", "title": "演示", "text": "第一句", "files": []}
```

`files` 每项为 `{"name": "...", "mime": "...", "data": "<base64>"}`。图片走内联 image block；其他文件落到
`state/data/uploads/<sessionId>/` 并把绝对路径写进提示词。限制：单文件 10 MB、单批 30 MB，超限返回 `413`。

返回 `{"taskId": "task-<uuid>", "threadId": "session-<id>", "steered": false}`。

错误码：

| HTTP | code | 含义 |
|---|---|---|
| 400 | `rejected` / `bad_request` / `bad_files` | 参数缺失或格式错误 |
| 403 | `cwd_forbidden` | 目录不在工作区白名单 |
| 409 | `session_busy` | 会话被另一个 Harness 进程持有，**未写入任何内容** |
| 413 | `attachment_rejected` | 附件超限或编码非法 |
| 502 | `prompt_failed` / `adapter_error` | 运行时或适配器失败 |

```http
GET <base>/api/local-task/<taskId>
```

```json
{"taskId": "task-<uuid>", "threadId": "session-<id>", "status": "running",
 "message": "DeepSeek Harness 正在处理", "turnId": null, "steered": false,
 "createdAt": 0, "finishedAt": null, "error": ""}
```

`status` ∈ `queued | running | completed | failed | interrupted | cancelled`。

## 余额

```http
余额响应同时带一个 `pricing` 对象（只描述时段，不含任何金额）：

```json
{"pricing": {"phase": "peak", "label": "忙时", "weekend": false,
             "localTime": "2026-10-26 15:00", "localWeekday": "周一", "zone": "UTC+08:00",
             "windows": "09:00–12:00、14:00–18:00", "days": "工作日",
             "nextSwitch": "2026-10-26 18:00", "nextSwitchEpoch": 1793008800, "secondsLeft": 10800,
             "note": "工作日 …", "invalid": ""}}
```

`phase` 只取 `peak` / `off-peak`；`invalid` 非空表示配置里的时段/星期写坏了、已回退到默认值。
桥不返回价格表，也不参与计费：金额始终来自 Platform 的原生余额。

GET <base>/api/balance[?refresh=1]
POST <base>/api/balance/refresh
```

```json
{
  "providerId": "deepseek-harness",
  "available": true,
  "status": "ok",
  "source": "harness-account",
  "updatedAt": 0,
  "stale": false,
  "entries": [{"currency": "CNY", "wallet": "normal", "amount": "0.0000000000000000"},
              {"currency": "CNY", "wallet": "bonus", "amount": "0"}],
  "detail": {"walletKinds": ["normal", "bonus"], "note": "..."},
  "ageSeconds": 0.0,
  "display": {"label": "余额 ¥0.00", "primary": {"currency": "CNY", "amount": "0.0000000000000000"}}
}
```

`available:false` 且 `status` 为 `auth_required | unavailable | unsupported` 时页面显示「余额暂不可用」。
刷新失败但存在旧快照时 `stale:true` 且 `updatedAt` 保持旧值。

## 交互（批准与提问）

```http
GET <base>/api/interactions
```

```json
{"session": "local", "startedAt": 0, "revision": 3, "available": true, "error": "",
 "requests": [{"id": "approval-<id>", "kind": "approval", "category": "web",
               "threadId": "session-<id>", "status": "pending",
               "title": "需要批准：pwsh", "message": "会话名",
               "help": "...", "questions": [], "fields": [],
               "actions": ["accept", "decline"], "details": "escalate sandbox ...",
               "openedAt": 0, "deadline": 0}]}
```

```http
POST <base>/api/interactions/respond
{"id": "approval-<id>", "action": "accept"}          # → allowed-once
{"id": "approval-<id>", "action": "decline"}         # → rejected
{"id": "questions-<id>", "action": "answer", "answers": {"<questionId>": "选项或自定义文字"}}
```

命中选项标签时按 `selected` 提交，否则作为 `custom` 提交。已被处理或不存在的请求返回 `404 interaction_missing`。

## 权限预设

```http
GET <base>/api/permissions[?threadId=<sessionId>]
```

```json
{"options": [{"value": "workspace-write", "name": "workspace-write", "description": "..."},
             {"value": "danger-full-access", "name": "danger-full-access", "description": "..."}],
 "defaultPreset": "workspace-write", "current": "workspace-write", "bridgeDefault": "workspace-write",
 "threadId": "session-<id>"}
```

`current` 只在该会话已在本运行时打开时返回，否则为 `null`（目录来自运行时自带的预设表，桥不硬编码）。

```http
POST <base>/api/permissions
{"threadId": "session-<id>", "preset": "danger-full-access"}   # 切换本会话
{"preset": "danger-full-access"}                                # 只改“新会话默认”
```

切换会先确保会话在本运行时打开（必要时 resume），再调用 Harness 的权限预设服务；结果写入会话日志的
`permission/preset` 与 `sandbox/mode`。未知预设返回 `400 unknown_preset`；会话被别的进程占用返回 `409 session_busy`。

新会话不需要额外调用：`POST /api/local-tasks` 的 `permissionPreset` 或桥的默认值会在**第一条消息之前**应用。

## 附件与文件

```http
GET <base>/api/attachment?path=<绝对路径>
```

只允许工作区白名单与上传目录内的文件；超过单文件上限返回 `413`。图片 `inline`，其他 `attachment`。

```http
POST <base>/api/open-attachment
{"path": "<绝对路径>"}
```

在主机上用默认程序打开该文件（仅 Windows）。

## 语音（可选）

```http
GET <base>/api/voice/config
```

```json
{"enabled": true, "configured": true, "voice": "<voice id>", "resource": "<resource id>",
 "preferences": {"voiceEnabled": true}}
```

`enabled` 来自 `DSH_BRIDGE_VOICE`，`configured` 表示凭据三项齐全；任一为假时页面不显示朗读按钮。

```http
POST <base>/api/voice/request
{"text": "要朗读的文字"}
```

```json
{"cached": true, "key": "<64 位十六进制>", "bytes": 10605}
```

同步合成（`urllib` 直连 SSE，120 秒超时）。失败码：`voice_not_configured`(503)、`voice_text_empty` / `voice_text_too_long`(400)、
`voice_provider_error` / `voice_provider_unavailable` / `voice_audio_too_large`(502)、`voice_disabled`(403)。

```http
GET <base>/api/voice/status?keys=<key1>,<key2>
→ {"cached": {"<key>": true}}
```

```http
GET <base>/api/voice/audio/<key>
→ audio/mpeg，Cache-Control: private, max-age=86400
```

`key` 必须是 64 位小写十六进制（`SHA-256(JSON.stringify([text, voice, resource, "v3-sse-mp3-rate25"]))`），否则 `404 voice_not_found`；
音频只从桥自己的缓存目录读取，不接受任何路径参数。

```http
GET <base>/api/preferences        → {"voiceEnabled": true}
POST <base>/api/preferences       {"voiceEnabled": false}
```

只接受已知键与正确类型；其余字段被忽略。关闭后服务端会拒绝新的语音请求。

## 其他

| 方法 | 路径 | 说明 |
|---|---|---|
| GET | `<base>/api/auth/config` | 页面启动配置（本地模式 `authRequired:false`） |
| GET | `<base>/api/pinned` | 预留：置顶/隐藏列表（当前为空） |
