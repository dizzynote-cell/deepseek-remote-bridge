# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Quiet launcher used by the desktop and startup shortcuts:
# start the bridge (and its tray icon) if needed, then open the page.
# -NoOpen starts everything without launching the browser (used by tests).
param([switch]$NoOpen)
. (Join-Path $PSScriptRoot 'bridge-common.ps1')

$root = Get-BridgeRoot
Write-Host "DeepSeek Remote Bridge: starting from $root"
try {
  Start-Bridge -Root $root
  Start-Tray -Root $root -Quiet
} catch {
  Write-Host "Start failed: $($_.Exception.Message)"
  Write-Host 'Run scripts\status-bridge.ps1 or check state\logs\bridge.err.log for details.'
  Start-Sleep -Seconds 4
  exit 1
}
Start-Sleep -Milliseconds 800
if (-not $NoOpen) { Start-Process (Get-BridgeUrl $root) }
