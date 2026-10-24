# 飞书登录与接入：安装引导（设计稿，尚未实现）

目标：**不在桥里保存任何密码**。网页身份由飞书 OAuth 提供，机器人消息走独立应用的长连接。
本文是接入时的施工图，当前版本没有实现，`capabilities` 里也不声称支持。

---

## 0. 为什么用飞书 OAuth 而不是自建登录

| 自建账号密码 | 飞书 OAuth |
|---|---|
| 要存密码哈希、找回、改密、防撞库 | 不存密码，桥只验签飞书回调 |
| 无法知道"这个人是谁" | 拿到企业内的 `open_id` / 姓名 / 头像 |
| 离职后仍可登录 | 企业停用飞书账号即失效 |
| 需要自己做登录限流、验证码 | 交给飞书，桥只需对 `code` 兑换做限流 |

桥只做三件事：校验 `state`、用 `code` 换用户信息、比对所有者白名单。

---

## 1. 在飞书开放平台创建应用

1. <https://open.feishu.cn/app> → 创建企业自建应用，记下 **App ID** 与 **App Secret**。
2. 添加「网页应用」能力，配置**重定向 URL**（必须与桥配置完全一致）：
   - 本机：`http://127.0.0.1:8766/api/auth/feishu/callback`
   - 反代：`https://<你的域名>/dsh/api/auth/feishu/callback`
3. 权限（最小集）：
   - `authen:user.id:read`（读取登录用户身份）
   - 若要发消息/审批卡片，另加 `im:message:send_as_bot`、`im:message:receive_v1`（事件）
4. 事件订阅：机器人消息用**长连接**（WebSocket），不要配公网回调地址。

> 长连接按应用维度消费事件：**不要**和现有 Codex 桥共用同一个飞书应用，否则同一条消息会被两个进程各领一次。见 [`feishu.md`](feishu.md)。

---

## 2. 桥侧配置（`config/.env`，不进仓库）

```ini
DSH_BRIDGE_FEISHU=1
FEISHU_APP_ID=cli_xxxxxxxxxxxxxxxx
FEISHU_APP_SECRET=xxxxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
FEISHU_REDIRECT_URI=https://<你的域名>/dsh/api/auth/feishu/callback
# 允许登录的飞书 open_id，逗号分隔；空表示任何人都能登录（不推荐）
FEISHU_OWNER_OPEN_IDS=ou_xxxxxxxxxxxxxxxxxxxxxxxxxxxxxx
# 桥会话 cookie 的签名密钥；留空则首次启动随机生成并写入 state/
FEISHU_SESSION_SECRET=
# 可选：只允许某个租户
FEISHU_TENANT_KEY=
```

密钥只在本机进程内使用；页面与日志都不出现 `app_secret`。

---

## 3. 登录流程（建议实现）

```text
浏览器 → GET /api/auth/feishu/start
         ├─ 生成 state = base64(nonce + 时间戳) + HMAC(secret)
         └─ 302 → https://open.feishu.cn/open-apis/authen/v1/index
                    ?app_id=...&redirect_uri=...&state=...

飞书 → 用户同意 → 302 回 /api/auth/feishu/callback?code=...&state=...
         ├─ 校验 state：签名正确、时间戳 < 10 分钟、未被用过（内存 LRU）
         ├─ POST /open-apis/auth/v3/app_access_token/internal（app_id + app_secret，缓存 2 小时）
         ├─ POST /open-apis/authen/v1/oidc/access_token（code → user_access_token）
         ├─ GET  /open-apis/authen/v1/user_info → { open_id, name, avatar_url, tenant_key }
         ├─ 比对 FEISHU_OWNER_OPEN_IDS（与 FEISHU_TENANT_KEY）
         └─ 302 → /dsh/ 并 Set-Cookie: dsh_session=<HMAC 签名负载>; HttpOnly; Secure; SameSite=Lax; Path=/dsh/
```

要点：

- `state` 用 HMAC 签名而不是服务端 session 表，重启不掉登录；nonce 只做一次性防重放。
- cookie 不落库：负载里只有 `open_id`、`tenant_key`、`exp`（建议 12 小时）+ 签名。
- 需要 `Secure`：所以必须 HTTPS；本机 `127.0.0.1` 允许非 Secure（浏览器把 localhost 当安全上下文）。
- 未登录的 `/api/*` 一律 401（`/api/auth/*` 与静态资源除外），页面跳转登录。
- 登出：`POST /api/auth/logout` 清 cookie；不需要调用飞书撤销。

---

## 4. 安全约束（必须一起实现）

| 项 | 要求 |
|---|---|
| CSRF | 保留现有的 `Origin`/`Sec-Fetch-Site` 检查；登录用 `state`，登出用 POST |
| 开放重定向 | 回调后只跳回配置的基址，绝不接受 `return_to` 参数 |
| 限流 | 按 IP 对 `/api/auth/feishu/callback` 做连续失败限流（例如 10 次/10 分钟） |
| 白名单 | 默认拒绝：`FEISHU_OWNER_OPEN_IDS` 为空时**只允许本机回环**，不允许远程登录 |
| Cookie | `HttpOnly` + `SameSite=Lax` + `Secure`（HTTPS 时）；不用 `localStorage` 存会话 |
| 日志 | 只记 `open_id` 后四位与结果，不记 token 与 code |
| 前端 | 不接收也不转发任何飞书 token；页面只知道自己"已登录" |

---

## 5. 机器人消息（同一应用或入口路由）

登录只解决"网页是谁"。要让飞书里也能下指令、收审批：

1. 事件订阅用长连接，只处理 `FEISHU_OWNER_OPEN_IDS` 的消息。
2. 每条消息映射到 `chat_id` 上次使用的 `session-<id>`；未绑定则新建并在回复里给出会话名。
3. 发送复用现有 `TaskRunner`：`session/prompt` → `session/wait` → 把终态回复发回。
4. 审批/提问：把 `/api/interactions` 的同一份请求渲染成互动卡片（`approve` / `reject` / `answer`），回调校验 `open_id` 后再回填。
5. 附件：飞书图片转 image block；文档下载到 `state/data/uploads/` 后把绝对路径写进提示词。

---

## 6. 验收清单

- [ ] 未登录访问 `/dsh/` 跳转飞书登录；登录后回原页面。
- [ ] 非白名单账号登录被拒绝，页面给出明确提示，不泄露白名单内容。
- [ ] `state` 被篡改、过期、重复使用时均拒绝。
- [ ] 登出后旧 cookie 立即失效（或被服务端拒绝）。
- [ ] 同一飞书应用没有被两个进程同时消费事件（检查日志里每条消息只处理一次）。
- [ ] 审批卡片只能由白名单账号操作，跨账号回复被拒绝。
- [ ] `config/.env`、`state/` 不在 git 中；日志无 token。

## 7. 与现有 Codex 桥的关系

- 两端**不要共用**飞书应用、cookie 名、会话 token 或任务队列。
- 如果第二阶段由入口统一登录：入口只把"已验证身份"以受控票据交给 DSH，DSH 仍按自己的白名单做第二次判断，不信任 `X-Provider`、`X-User` 之类的自报头。
