# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# One-shot watchdog, meant to be run by Task Scheduler every few minutes:
# if this bridge is not healthy, start it again. Never touches other bridges.
. (Join-Path $PSScriptRoot 'bridge-common.ps1')

$root = Get-BridgeRoot
$logDir = Join-Path (Get-BridgeState $root) 'logs'
if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
$log = Join-Path $logDir 'watchdog.log'

function Write-Watchdog([string]$message) {
  try { Add-Content -Path $log -Value ("{0} {1}" -f (Get-Date -Format 'yyyy-MM-dd HH:mm:ss'), $message) } catch { }
}

if (Test-BridgeHealth -Root $root -TimeoutSeconds 8) { return }

$running = Get-RunningBridge $root
if ($running) {
  Write-Watchdog ("health check failed for pid {0}; restarting" -f $running.ProcessId)
  try { Stop-Bridge -Root $root } catch { Write-Watchdog ("stop failed: " + $_.Exception.Message) }
} else {
  Write-Watchdog 'bridge is not running; starting it'
}
try {
  Start-Bridge -Root $root
  Start-Tray -Root $root -Quiet
  Write-Watchdog 'restart requested'
} catch {
  Write-Watchdog ("start failed: " + $_.Exception.Message)
}
