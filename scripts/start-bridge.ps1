# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Start the local bridge in the background, wait for health, and make sure the
# notification-area icon exists (unless -NoTray is given).
param([switch]$NoTray)
. (Join-Path $PSScriptRoot 'bridge-common.ps1')
$root = Get-BridgeRoot
Start-Bridge -Root $root
if (-not $NoTray) {
  try { Start-Tray -Root $root -Quiet } catch { Write-Host "托盘未启动：$($_.Exception.Message)" }
}