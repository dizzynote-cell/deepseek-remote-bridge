# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Create or remove the Windows shortcuts for this bridge:
#   - Desktop shortcut  : starts the bridge (and tray) and opens the page
#   - Startup shortcut  : same, at every Windows sign-in
#
#   .\scripts\install-shortcuts.ps1 -Desktop
#   .\scripts\install-shortcuts.ps1 -Startup
#   .\scripts\install-shortcuts.ps1 -Desktop -Startup
#   .\scripts\install-shortcuts.ps1 -Remove
#
# Only this repository is referenced; the Codex bridge is never touched.

param(
  [switch]$Desktop,
  [switch]$Startup,
  [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$launcher = Join-Path $root 'scripts\launch-bridge.ps1'
$icon = Join-Path $root 'assets\dphlogo.ico'
$powershell = Join-Path $env:SystemRoot 'System32\WindowsPowerShell\v1.0\powershell.exe'
$name = 'DeepSeek Remote Bridge.lnk'

if (-not (Test-Path $launcher)) { throw "Missing launcher: $launcher" }
if (-not $Desktop -and -not $Startup -and -not $Remove) {
  $Desktop = $true
  $Startup = $true
}

$desktopPath = [Environment]::GetFolderPath('Desktop')
$startupPath = [Environment]::GetFolderPath('Startup')
$shell = New-Object -ComObject WScript.Shell

function New-BridgeShortcut {
  param([string]$directory, [string]$description)
  if (-not (Test-Path $directory)) { throw "Folder not found: $directory" }
  $linkPath = Join-Path $directory $name
  $shortcut = $shell.CreateShortcut($linkPath)
  $shortcut.TargetPath = $powershell
  $shortcut.Arguments = '-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File "' + $launcher + '"'
  $shortcut.WorkingDirectory = $root
  if (Test-Path $icon) { $shortcut.IconLocation = "$icon,0" }
  $shortcut.Description = $description
  $shortcut.WindowStyle = 7
  $shortcut.Save()
  Write-Host "Created: $linkPath"
}

function Remove-BridgeShortcut {
  param([string]$directory)
  $linkPath = Join-Path $directory $name
  if (Test-Path $linkPath) {
    Remove-Item $linkPath -Force
    Write-Host "Removed: $linkPath"
  }
}

if ($Remove) {
  Remove-BridgeShortcut $desktopPath
  Remove-BridgeShortcut $startupPath
  return
}
if ($Desktop) { New-BridgeShortcut -directory $desktopPath -description 'Start the DeepSeek Remote Bridge and open its page' }
if ($Startup) { New-BridgeShortcut -directory $startupPath -description 'Start the DeepSeek Remote Bridge at Windows sign-in' }

Write-Host ''
Write-Host 'Done. The shortcut runs scripts\launch-bridge.ps1 (hidden window), which starts the'
Write-Host 'bridge plus its tray icon and opens the page. Use scripts\stop-bridge.ps1 to stop it.'
