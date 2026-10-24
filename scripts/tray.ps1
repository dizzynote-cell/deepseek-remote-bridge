# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Tray menu for this bridge only: start/stop/restart/open and exit.
# It never touches other Python processes, the Codex bridge, or the harness.
# Run with Windows PowerShell (STA):
#   powershell -NoProfile -STA -ExecutionPolicy Bypass -File scripts\tray.ps1

param([switch]$Hidden)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

. (Join-Path $PSScriptRoot 'bridge-common.ps1')
$root = Get-BridgeRoot
$url = Get-BridgeUrl $root

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = [System.Drawing.SystemIcons]::Application
$notify.Text = 'DeepSeek Remote Bridge'
$notify.Visible = $true

$menu = New-Object System.Windows.Forms.ContextMenuStrip

$state = New-Object System.Windows.Forms.ToolStripMenuItem
$state.Text = '状态：检查中…'
$state.Enabled = $false
[void]$menu.Items.Add($state)
[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))

$open = New-Object System.Windows.Forms.ToolStripMenuItem
$open.Text = '打开页面'
$open.add_Click({ Start-Process $url })
[void]$menu.Items.Add($open)

$start = New-Object System.Windows.Forms.ToolStripMenuItem
$start.Text = '启动'
$start.add_Click({
  try { Start-Bridge -Root $root } catch { [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'DeepSeek Remote Bridge') }
})
[void]$menu.Items.Add($start)

$stop = New-Object System.Windows.Forms.ToolStripMenuItem
$stop.Text = '停止'
$stop.add_Click({ Stop-Bridge -Root $root })
[void]$menu.Items.Add($stop)

$restart = New-Object System.Windows.Forms.ToolStripMenuItem
$restart.Text = '重启'
$restart.add_Click({
  try {
    Stop-Bridge -Root $root
    Start-Sleep -Seconds 1
    Start-Bridge -Root $root
  } catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'DeepSeek Remote Bridge')
  }
})
[void]$menu.Items.Add($restart)

[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))

$exit = New-Object System.Windows.Forms.ToolStripMenuItem
$exit.Text = '退出托盘（不停止桥）'
$exit.add_Click({
  $notify.Visible = $false
  $notify.Dispose()
  [System.Windows.Forms.Application]::Exit()
})
[void]$menu.Items.Add($exit)

$notify.ContextMenuStrip = $menu

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.add_Tick({
  $process = Get-RunningBridge $root
  if (-not $process) {
    $state.Text = '状态：未运行'
    $notify.Text = 'DeepSeek Remote Bridge（未运行）'
    return
  }
  if (Test-BridgeHealth -Root $root -TimeoutSeconds 2) {
    $state.Text = "状态：运行中（PID $($process.ProcessId)）"
    $notify.Text = "DeepSeek Remote Bridge（PID $($process.ProcessId)）"
  } else {
    $state.Text = "状态：进程在但健康检查失败（PID $($process.ProcessId)）"
    $notify.Text = 'DeepSeek Remote Bridge（健康检查失败）'
  }
})
$timer.Start()

if (-not $Hidden) {
  $notify.ShowBalloonTip(3000, 'DeepSeek Remote Bridge', "托盘已启动：$url", [System.Windows.Forms.ToolTipIcon]::Info)
}

[System.Windows.Forms.Application]::Run()
$timer.Stop()
$notify.Visible = $false
$notify.Dispose()
