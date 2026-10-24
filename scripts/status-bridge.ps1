# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Report the bridge process, URL and health without changing anything.
. (Join-Path $PSScriptRoot 'bridge-common.ps1')
$root = Get-BridgeRoot
$process = Get-RunningBridge $root
$url = Get-BridgeUrl $root
if (-not $process) {
  Write-Host "桥未运行。期望地址：$url"
  exit 1
}
Write-Host "桥进程：PID $($process.ProcessId)"
Write-Host "页面地址：$url"
try {
  $health = Invoke-WebRequest -Uri "${url}api/health" -TimeoutSec 5 -UseBasicParsing | ConvertFrom-Json
  Write-Host "健康：ok=$($health.ok) 适配器=$($health.adapter.ok) 运行时=$(($health.runtime.routes | ForEach-Object { $_.route }) -join ', ')"
} catch {
  Write-Host '健康检查失败：' $_.Exception.Message
  exit 1
}