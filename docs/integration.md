# 双桥整合说明（交给 Codex）

本文件描述 DSH 桥为第二阶段预留的接口与隔离边界。**当前只实现 DSH 侧**，没有任何跨桥网关，也没有部署。

## 1. 页面入口

- 左上角 LOGO 是按钮：`#provider-toggle`（`aria-haspopup="menu"`，可键盘聚焦），点开后 `#provider-menu`（`role="menu"`）。
- 当前项：DeepSeek Harness，`aria-current="true"`。
- 另一项：Codex，`disabled` + `aria-disabled="true"`，文案说明由入口页统一切换，**没有** href，不会产生无效跳转。
- 入口页接管时，把 Codex 项换成真实跳转（或由入口页渲染自己的切换器），并保留当前项的 `aria-current`。

### 让菜单里的 Codex 变成真实跳转

DSH 桥侧只需要一个配置，菜单项就会从"禁用"变成"可点"：

```ini
DSH_BRIDGE_SIBLING_PROVIDER=codex
DSH_BRIDGE_SIBLING_NAME=Codex
DSH_BRIDGE_SIBLING_URL=https://<入口页域名>/
```

- 校验：只接受 `http(s)://host/...`；空值、相对路径、`javascript:` 都会保持禁用，不会出现死链。
- 页面的实现：启动时读 `/api/bridge-info`，把 `logoMenu.targets` 里 `current:false` 且
  `enabled:true` 的那一项接上整页跳转；未配置时保持原来的置灰文案。
- DSH 不保存"当前后端"到 Codex 的存储里；各桥自己保存最后会话与草稿。

如果由入口页直接注入切换逻辑，唯一需要读的接口是：

```http
GET <base>/api/bridge-info
→ {"providerId": "deepseek-harness", "displayName": "DeepSeek Harness",
   "logoMenu": {"current": "deepseek-harness", "targets": [...]}}
```

## 2. 基址

- 服务端支持 `/` 与 `/dsh/`（`DSH_BRIDGE_BASE_PATH`），也可以挂更深的前缀。
- 页面内资源全部相对路径；请求统一经 `bridgeFetch` 加前缀。
- **前缀之外的路径返回 404**：同源挂载时不会把 `/api/*` 误发给 DSH 桥。
- 反向代理若做前缀剥离，请把 `<base>` 设为剥离后的路径（例如 `/`），否则页面请求会对不上。

推荐形态（第二阶段）：

```text
https://<entry-host>/            → 现有入口（Codex 桥）
https://<entry-host>/dsh/        → DSH 桥（本服务，DSH_BRIDGE_BASE_PATH=/dsh/）
```

`/dsh` 会 302 到 `/dsh/`；静态资源、接口、下载、深链都跟随前缀。

## 3. 身份与数据隔离

- DSH 桥只监听回环；入口页若做公网访问，请由入口自己做认证，**不要**把 DSH 桥直接暴露。
- DSH 桥不校验 Codex 的 cookie/session；也不信任任何 `X-Provider` 头。
- 会话与任务都用原生 ID：`session-<uuid>` / `task-<uuid>`；桥内部已按 `sessionId` 区分，不接受跨后端的 ID。
- 写请求有同源检查（`Origin` 必须与 `Host` 一致，`Sec-Fetch-Site` 必须是 `same-origin`/`none`）。入口页若代发请求，需要从同源发起。

## 4. 余额与额度语义

- DSH：`/api/balance`，真实账户余额（钱包、币种、十进制字符串、更新时间、是否过期）。
- Codex：沿用 `/api/quota`（订阅额度、重置时间）。
- 两者不互相换算；入口页在切换 provider 时必须分别渲染，不要把 DSH 余额塞进额度百分比组件。
- 失败语义：DSH 返回 `available:false` + `status`（`auth_required`/`unavailable`/`unsupported`），入口页应显示「余额暂不可用」，不要填 0。

## 5. 状态与草稿

- DSH 页面把「最后打开的会话」放在 URL 的 `?thread=<sessionId>`；入口页切换时应保存各自最后会话，切回时恢复。
- 输入框草稿与待发附件只存在于当前页面的内存/DOM 中，切换 provider 不应自动发送，也不应跨后端搬运。
- 切换不重启任何进程：DSH 桥与 Codex 桥各自独立运行。

## 6. 服务与端口

| 服务 | 建议端口 | 说明 |
|---|---|---|
| Codex 本机桥 | 8765（既有） | 不要改 |
| Codex 云后端 | 8780（既有） | 不要改 |
| DSH 本机桥 | 8766（默认） | 可配置 |
| DSH 云后端 | 8781（预留，未实现） | 本阶段不部署 |

启动前先检查端口占用；DSH 桥不会终止占用端口的未知进程。

## 7. 已知缺口

- 本版没有飞书接入；如果第二阶段要统一飞书应用，只能有一个进程持有长连接，并按已保存的绑定路由。
- 本版没有云端程序；`cloud/` 目录未包含，也没有 `SYNC_TOKEN` 之类的共享密钥。
- 本版没有取消能力；入口页不要把「停止」按钮映射到 DSH 会话。
