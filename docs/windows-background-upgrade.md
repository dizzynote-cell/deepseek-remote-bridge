# Windows 后台启动与托盘升级

本次修复两个问题：保活任务周期性弹出空白 PowerShell/CMD 窗口，以及开机后出现两个 DeepSeek 托盘图标。

## 修复后的行为

- 桥及托盘在创建时通过 Windows 启动参数隐藏控制台，保留日志输出。
- 定时保活使用 `scripts/watchdog-hidden.vbs` 隐藏启动自己的 PowerShell 检查脚本，退出码仍交给任务计划程序；不会额外打开控制台。
- 托盘按安装目录使用 Windows 单实例互斥锁。开机快捷方式、保活任务或手动启动同时触发，也只会创建一个图标。
- 只有实际持有托盘的进程写 `state/tray.pid`，并发启动后立即退出的进程不会覆盖记录。

## 已安装用户如何升级

先更新源码；有本地修改时先保留修改，再合并新版，不要直接覆盖自己的配置。

如果曾安装定时保活任务，在项目目录重新运行：

```powershell
.\scripts\install-watchdog.ps1
```

若原来设置的间隔不是默认的 3 分钟，请保留自己的间隔，例如：

```powershell
.\scripts\install-watchdog.ps1 -Minutes 5
```

这会更新当前用户的 `DeepSeek Remote Bridge Watchdog` 任务动作。只更新源码不会把旧任务中直接启动 PowerShell 的动作改成隐藏启动器。不要删除 `scripts/watchdog-hidden.vbs`；它需要和 `watchdog-once.ps1` 放在同一目录。

然后在旧托盘图标上右键，选择「退出托盘（不停止桥）」。若原先有两个图标，对两个旧图标都执行该操作，再运行：

```powershell
.\scripts\start-tray.ps1
```

这一步只替换托盘界面，桥和正在执行的任务继续运行。不要选择「停止桥」或「重启桥」。如果旧版已留下一个承载桥的 CMD 窗口，不要直接点叉关闭；等待当前任务结束后，再安排一次自己的 DSH 桥重启，让新的隐藏启动方式接管。

## 检查

```powershell
.\scripts\status-bridge.ps1
schtasks /Query /TN "DeepSeek Remote Bridge Watchdog" /V /FO LIST
```

托盘应只有一个图标，桥健康正常；保活任务动作应指向系统 `wscript.exe` 和本项目的 `watchdog-hidden.vbs`。原来的开机快捷方式和保活任务可以同时保留，不需要关闭保活功能来避免重复图标。

所有脚本只管理自己的 DSH 项目，不需要重启 Codex 桥或修改云端服务。个人配置、令牌、日志和实际会话数据继续保存在忽略的私有目录，不应加入公开仓库。
