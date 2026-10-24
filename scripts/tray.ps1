# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Windows notification-area icon for this bridge only.
# It starts/stops/restarts exactly the bridge recorded in state/bridge.pid and
# never touches other Python processes, the Codex bridge, or the harness.
#
# Start it through scripts\start-tray.ps1 (or "DeepSeek Remote Bridge.cmd").
# Running it directly is fine too:  powershell -NoProfile -STA -File tray.ps1

param([switch]$Hidden)

Add-Type -AssemblyName System.Windows.Forms
Add-Type -AssemblyName System.Drawing

. (Join-Path $PSScriptRoot 'bridge-common.ps1')

$root = Get-BridgeRoot
$url = Get-BridgeUrl $root
$logDir = Join-Path (Get-BridgeState $root) 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$logFile = Join-Path $logDir 'tray.log'
function Write-TrayLog {
  param([string]$Message)
  try { Add-Content -Path $logFile -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $Message) } catch { }
}
Write-TrayLog ("start pid={0} hidden={1}" -f $PID, $Hidden)

# Brand icon drawn at runtime, so no binary asset is shipped.
function New-BridgeTrayIcon {
  $size = 32
  $bitmap = New-Object System.Drawing.Bitmap $size, $size
  $graphics = [System.Drawing.Graphics]::FromImage($bitmap)
  $graphics.SmoothingMode = [System.Drawing.Drawing2D.SmoothingMode]::AntiAlias
  $graphics.Clear([System.Drawing.Color]::Transparent)
  $start = New-Object System.Drawing.Point 0, 0
  $end = New-Object System.Drawing.Point $size, $size
  $from = [System.Drawing.Color]::FromArgb(255, 87, 217, 154)
  $to = [System.Drawing.Color]::FromArgb(255, 36, 138, 103)
  $brush = New-Object System.Drawing.Drawing2D.LinearGradientBrush($start, $end, $from, $to)
  $radius = 9
  $diameter = $radius * 2
  $path = New-Object System.Drawing.Drawing2D.GraphicsPath
  $path.AddArc(0, 0, $diameter, $diameter, 180, 90)
  $path.AddArc($size - $diameter, 0, $diameter, $diameter, 270, 90)
  $path.AddArc($size - $diameter, $size - $diameter, $diameter, $diameter, 0, 90)
  $path.AddArc(0, $size - $diameter, $diameter, $diameter, 90, 90)
  $path.CloseFigure()
  $graphics.FillPath($brush, $path)
  $font = New-Object System.Drawing.Font 'Segoe UI', 15, ([System.Drawing.FontStyle]::Bold), ([System.Drawing.GraphicsUnit]::Pixel)
  $format = New-Object System.Drawing.StringFormat
  $format.Alignment = [System.Drawing.StringAlignment]::Center
  $format.LineAlignment = [System.Drawing.StringAlignment]::Center
  $rect = New-Object System.Drawing.RectangleF 0, 0, $size, $size
  $graphics.DrawString('D', $font, [System.Drawing.Brushes]::White, $rect, $format)
  $graphics.Dispose()
  $icon = [System.Drawing.Icon]::FromHandle($bitmap.GetHicon())
  return @{ Icon = $icon; Bitmap = $bitmap; Font = $font; Brush = $brush; Path = $path }
}

$assets = New-BridgeTrayIcon

$notify = New-Object System.Windows.Forms.NotifyIcon
$notify.Icon = $assets.Icon
$notify.Text = 'DeepSeek Remote Bridge'
$notify.Visible = $true
Write-TrayLog "notifyicon created pid=$PID visible=$($notify.Visible) text=$($notify.Text)"

$menu = New-Object System.Windows.Forms.ContextMenuStrip

function Add-TrayItem {
  param([string]$Text, [scriptblock]$OnClick, [switch]$Disabled)
  $item = New-Object System.Windows.Forms.ToolStripMenuItem
  $item.Text = $Text
  if ($Disabled) { $item.Enabled = $false }
  if ($OnClick) {
    $action = $OnClick
    $item.add_Click({ & $action })
  }
  [void]$menu.Items.Add($item)
  return $item
}

$stateItem = New-Object System.Windows.Forms.ToolStripMenuItem
$stateItem.Text = '状态：检查中…'
$stateItem.Enabled = $false
[void]$menu.Items.Add($stateItem)
[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))

Add-TrayItem '打开页面' { Start-Process $url } | Out-Null
Add-TrayItem '打开日志目录' { if (Test-Path $logDir) { Start-Process explorer.exe $logDir } } | Out-Null
Add-TrayItem '任务栏显示设置…' { Start-Process 'ms-settings:taskbar' } | Out-Null
[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))
Add-TrayItem '启动桥' {
  try { Start-Bridge -Root $root } catch { [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'DeepSeek Remote Bridge') }
} | Out-Null
Add-TrayItem '停止桥' { Stop-Bridge -Root $root } | Out-Null
Add-TrayItem '重启桥' {
  try {
    Stop-Bridge -Root $root
    Start-Sleep -Seconds 1
    Start-Bridge -Root $root
  } catch {
    [System.Windows.Forms.MessageBox]::Show($_.Exception.Message, 'DeepSeek Remote Bridge')
  }
} | Out-Null
[void]$menu.Items.Add((New-Object System.Windows.Forms.ToolStripSeparator))
Add-TrayItem '退出托盘（不停止桥）' {
  $notify.Visible = $false
  $notify.Dispose()
  [System.Windows.Forms.Application]::Exit()
} | Out-Null

$notify.ContextMenuStrip = $menu
$notify.add_DoubleClick({ Write-TrayLog 'double-click open page'; Start-Process $url })

$timer = New-Object System.Windows.Forms.Timer
$timer.Interval = 5000
$timer.add_Tick({
  $process = Get-RunningBridge $root
  if (-not $process) {
    if ($stateItem.Text -ne '状态：未运行') { Write-TrayLog 'status: bridge not running' }
    $stateItem.Text = '状态：未运行'
    $notify.Text = "DeepSeek Remote Bridge（未运行）`n双击打开页面"
    return
  }
  if (Test-BridgeHealth -Root $root -TimeoutSeconds 2) {
    if ($stateItem.Text -notlike "状态：运行中*") { Write-TrayLog ("status: running pid={0}" -f $process.ProcessId) }
    $stateItem.Text = "状态：运行中（PID $($process.ProcessId)）"
    $notify.Text = "DeepSeek Remote Bridge（运行中 PID $($process.ProcessId)）`n双击打开页面"
  } else {
    if ($stateItem.Text -notlike '状态：进程在*') { Write-TrayLog ("status: unhealthy pid={0}" -f $process.ProcessId) }
    $stateItem.Text = "状态：进程在，健康检查失败（PID $($process.ProcessId)）"
    $notify.Text = 'DeepSeek Remote Bridge（健康检查失败）'
  }
})
$timer.Start()

if (-not $Hidden) {
  $notify.ShowBalloonTip(
    6000,
    'DeepSeek Remote Bridge',
    "托盘图标已启动（绿色 D）。若任务栏没有直接显示，点任务栏的 ^ 展开并把它拖到任务栏。`n$url",
    [System.Windows.Forms.ToolTipIcon]::Info
  )
}

Write-TrayLog "menu ready; entering message loop"
[System.Windows.Forms.Application]::Run()
Write-TrayLog "message loop ended"

$timer.Stop()
$timer.Dispose()
$notify.Visible = $false
$notify.Dispose()
$assets.Bitmap.Dispose()
$assets.Font.Dispose()
$assets.Brush.Dispose()
$assets.Path.Dispose()
