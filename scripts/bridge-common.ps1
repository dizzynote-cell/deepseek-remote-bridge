# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Shared helpers for the launcher scripts. Dot-source from the other scripts.
# These helpers only ever touch the bridge's own PID file and its own
# "bridge.py" process; they never match or stop other Python or Codex processes.

$ErrorActionPreference = 'Stop'

function Get-BridgeRoot {
  return (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
}

function Get-BridgeState {
  param([string]$Root)
  $state = Join-Path $Root 'state'
  if (-not (Test-Path $state)) { New-Item -ItemType Directory -Path $state -Force | Out-Null }
  return $state
}

function Get-BridgePort {
  param([string]$Root)
  $envFile = Join-Path $Root 'config/.env'
  $port = 8766
  if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
      if ($line -match '^\s*DSH_BRIDGE_PORT\s*=\s*(\d+)') { $port = [int]$Matches[1] }
    }
  }
  return $port
}

function Get-BridgeUrl {
  param([string]$Root)
  $port = Get-BridgePort $Root
  $base = '/'
  $envFile = Join-Path $Root 'config/.env'
  if (Test-Path $envFile) {
    foreach ($line in Get-Content $envFile) {
      if ($line -match '^\s*DSH_BRIDGE_BASE_PATH\s*=\s*(\S+)') { $base = $Matches[1] }
    }
  }
  if (-not $base.StartsWith('/')) { $base = "/$base" }
  if (-not $base.EndsWith('/')) { $base = "$base/" }
  return "http://127.0.0.1:$port$base"
}

function Get-BridgePidFile {
  param([string]$Root)
  return (Join-Path $Root 'state/bridge.pid')
}

function Test-BridgeProcess {
  param([int]$ProcessId, [string]$Root)
  if (-not $ProcessId) { return $null }
  $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
  if (-not $process) { return $null }
  $command = [string]$process.CommandLine
  $expected = Join-Path $Root 'bridge.py'
  if ($command -like "*$expected*") { return $process }
  if ($command -like '*bridge.py*' -and $command -like "*$(Split-Path $Root -Leaf)*") { return $process }
  return $null
}

function Get-RunningBridge {
  param([string]$Root)
  $pidFile = Get-BridgePidFile $Root
  if (-not (Test-Path $pidFile)) { return $null }
  $saved = (Get-Content $pidFile -ErrorAction SilentlyContinue | Select-Object -First 1)
  if (-not $saved) { return $null }
  $process = Test-BridgeProcess -ProcessId ([int]$saved) -Root $Root
  if ($process) { return $process }
  Remove-Item $pidFile -Force -ErrorAction SilentlyContinue
  return $null
}

function Test-BridgeHealth {
  param([string]$Root, [int]$TimeoutSeconds = 3)
  try {
    $url = (Get-BridgeUrl $Root) + 'api/health'
    $response = Invoke-WebRequest -Uri $url -TimeoutSec $TimeoutSeconds -UseBasicParsing
    return ($response.StatusCode -eq 200)
  } catch {
    return $false
  }
}

function Wait-BridgeHealth {
  param([string]$Root, [int]$Seconds = 60)
  for ($i = 0; $i -lt $Seconds; $i++) {
    if (Test-BridgeHealth -Root $Root) { return $true }
    Start-Sleep -Seconds 1
  }
  return $false
}

function Assert-PortFree {
  param([int]$Port)
  $listener = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
  if ($listener) {
    $owner = ($listener | Select-Object -First 1).OwningProcess
    $name = (Get-Process -Id $owner -ErrorAction SilentlyContinue).ProcessName
    throw "端口 $Port 已被占用（PID $owner/$name）。不会终止未知进程；请先释放端口或修改 config/.env 的 DSH_BRIDGE_PORT。"
  }
}

function Start-Bridge {
  param([string]$Root)
  $existing = Get-RunningBridge $Root
  if ($existing) {
    Write-Host "桥已在运行（PID $($existing.ProcessId)）：$(Get-BridgeUrl $Root)"
    return
  }
  $port = Get-BridgePort $Root
  Assert-PortFree -Port $port
  $state = Get-BridgeState $Root
  $logDir = Join-Path $state 'logs'
  if (-not (Test-Path $logDir)) { New-Item -ItemType Directory -Path $logDir -Force | Out-Null }
  $outLog = Join-Path $logDir 'bridge.out.log'
  $errLog = Join-Path $logDir 'bridge.err.log'
  $python = (Get-Command python -ErrorAction SilentlyContinue)
  if (-not $python) { throw '找不到 python。请先安装 Python 3.10+ 并加入 PATH。' }
  $arguments = @('bridge.py')
  $process = Start-Process -FilePath $python.Source -ArgumentList $arguments -WorkingDirectory $Root -WindowStyle Hidden `
    -RedirectStandardOutput $outLog -RedirectStandardError $errLog -PassThru
  Set-Content -Path (Get-BridgePidFile $Root) -Value $process.Id -Encoding ascii
  if (Wait-BridgeHealth -Root $Root -Seconds 90) {
    Write-Host "桥已启动（PID $($process.Id)）：$(Get-BridgeUrl $Root)"
  } else {
    Write-Host "桥进程已启动（PID $($process.Id)），但健康检查未通过。查看日志：$errLog"
  }
}

function Stop-Bridge {
  param([string]$Root)
  $process = Get-RunningBridge $Root
  if (-not $process) {
    Write-Host '桥未在运行。'
    Remove-Item (Get-BridgePidFile $Root) -Force -ErrorAction SilentlyContinue
    return
  }
  Write-Host "正在停止桥（PID $($process.ProcessId)）…"
  Stop-Process -Id $process.ProcessId -Force -ErrorAction SilentlyContinue
  for ($i = 0; $i -lt 20; $i++) {
    if (-not (Get-Process -Id $process.ProcessId -ErrorAction SilentlyContinue)) { break }
    Start-Sleep -Milliseconds 500
  }
  Remove-Item (Get-BridgePidFile $Root) -Force -ErrorAction SilentlyContinue
  Write-Host '桥已停止。'
}
