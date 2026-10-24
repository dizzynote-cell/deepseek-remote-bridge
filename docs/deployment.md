# 部署建议与回退

本文件只给 **Codex 第二阶段**参考。本阶段（DSH 独立开发）没有部署任何服务器、没有重启现有桥。

## 1. 边界

- DeepSeek Harness 与它的运行时**只在你自己的主力电脑上**。桥必须和 Harness 同机，因为 SDK 运行时是本地子进程、会话存储与写锁也在本机。
- 因此「部署」只有两件事：主机上的进程托管 + 入口页的路径接入。**没有**把模型或文件执行搬到云端的方案。
- 现有 Codex 桥（本机 8765 / 云 8780）完全不动；DSH 用自己的端口、自己的 `state/`。

## 2. 推荐形态（低侵入）

```text
入口（现有 Codex 页面）
  ├─ /            → Codex 桥（保持现状）
  └─ /dsh/        → 反代到 127.0.0.1:8766（DSH 桥，DSH_BRIDGE_BASE_PATH=/dsh/）
```

反代两种写法任选其一，**不要混用**：

| 写法 | DSH 配置 | 说明 |
|---|---|---|
| 保留前缀 | `DSH_BRIDGE_BASE_PATH=/dsh/` | 反代把 `/dsh/*` 原样转发到 `127.0.0.1:8766/dsh/*` |
| 剥掉前缀 | `DSH_BRIDGE_BASE_PATH=/` | 反代把 `/dsh/*` 改写成 `/*` 再转发 |

示例（Caddy，剥掉前缀的写法）：

```caddy
handle /dsh/* {
    uri strip_prefix /dsh
    reverse_proxy 127.0.0.1:8766
}
```

- 不要给 `/api/*` 做兜底转发：DSH 桥在非 `/` 基址下对前缀外路径返回 404，这正是隔离点。
- 不要共享 Cookie 名、不要让浏览器直接携带 Harness 凭据；桥不读 Codex 的 session。

## 3. 主机进程托管

已有脚本（本仓库）：

```powershell
.\scripts\start-bridge.ps1      # 后台启动并等待 /api/health
.\scripts\status-bridge.ps1     # PID、地址、健康、运行时路由
.\scripts\stop-bridge.ps1       # 只停 state/bridge.pid 里记录的那个 bridge.py
.\scripts\watchdog.ps1          # 健康检查失败时重启（只重启自己的进程）
.\scripts\install-autostart.ps1 # 开机启动快捷方式（可 -Remove）
```

要点：

- 进程身份按 `bridge.py` + 仓库路径匹配，**不会**按进程名杀 Python，也不会碰 Codex 的进程。
- 日志在 `state/logs/bridge.out.log` / `bridge.err.log`；运行时的 stderr 在 `state/logs/runtime-*.log`。
- 启动前 `scripts/start-bridge.ps1` 会检查端口占用并拒绝启动，不会终止占用端口的未知进程。

## 4. 上线前检查

1. `python bridge.py --check` 退出码 0（真的拉起一次 Harness 运行时并访问适配器）。
2. 浏览器打开 `<base>/api/bridge-info`：`providerId` 为 `deepseek-harness`，`capabilities` 与预期一致。
3. `<base>/api/balance` 返回 `available:true` 或明确的 `auth_required`。
4. 页面在 `/dsh/` 下所有资源 200、`/` 下的 `/api/*` 404（隔离有效）。
5. 现有 Codex 桥的 `/api/threads`、额度接口行为不变。
6. 回退命令已经准备好（见下），并且知道要停哪个服务。

## 5. 回退

DSH 侧（不影响 Codex）：

```powershell
# 1) 停桥（只停自己的 PID）
.\scripts\stop-bridge.ps1
# 2) 取消开机启动
.\scripts\install-autostart.ps1 -Remove
# 3) 需要回到上一个版本时
git -C <repo> log --oneline -5
git -C <repo> checkout <上一个 commit>      # 或 git revert <commit>
# 4) 清掉桥自己的状态（令牌、补丁、缓存、上传、日志）
Remove-Item -Recurse -Force <repo>\state
```

入口侧：删掉 `/dsh/*` 的反代规则并重载入口服务，即回到只有 Codex 的状态。DSH 桥即使还在跑，也不会被访问。

数据回退：DSH 桥只写自己的 `state/`；它通过 Harness 接口续写的会话属于 Harness 自己的会话库，回退桥不会删除会话，也不会改写历史。若需要恢复某个会话，用 Harness 桌面端打开即可。

## 6. 不做的事

- 不把 DSH 的运行时、模型密钥或账户令牌搬到服务器。
- 不在同一飞书应用上再开一条长连接（会重复消费事件）。
- 不在没有当次授权的情况下重启入口或 Codex 服务。
