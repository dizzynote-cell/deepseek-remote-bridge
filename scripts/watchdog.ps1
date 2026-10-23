# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Keep the bridge alive: restart only when its own health check fails.
param([int]$IntervalSeconds = 30)
. (Join-Path $PSScriptRoot 'bridge-common.ps1')
$root = Get-BridgeRoot
$logDir = Join-Path (Get-BridgeState $root) 'logs'
$log = Join-Path $logDir 'watchdog.log'
while ($true) {
  if (-not (Test-BridgeHealth -Root $root -TimeoutSeconds 5)) {
    $stamp = Get-Date -Format 'yyyy-MM-dd HH:mm:ss'
    Add-Content -Path $log -Value "$stamp health check failed; restarting"
    try { Stop-Bridge -Root $root } catch { Add-Content -Path $log -Value "$stamp stop failed: $($_.Exception.Message)" }
    try { Start-Bridge -Root $root } catch { Add-Content -Path $log -Value "$stamp start failed: $($_.Exception.Message)" }
  }
  Start-Sleep -Seconds $IntervalSeconds
}