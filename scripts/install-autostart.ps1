# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Add (or remove) a Startup shortcut for this bridge only.
param([switch]$Remove)
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$startup = [Environment]::GetFolderPath('Startup')
$link = Join-Path $startup 'DeepSeek Remote Bridge.lnk'
if ($Remove) {
  if (Test-Path $link) { Remove-Item $link -Force; Write-Host '已移除开机启动。' } else { Write-Host '没有开机启动项。' }
  exit 0
}
$target = Join-Path $root 'DeepSeek Remote Bridge.cmd'
$shell = New-Object -ComObject WScript.Shell
$shortcut = $shell.CreateShortcut($link)
$shortcut.TargetPath = $target
$shortcut.WorkingDirectory = $root
$shortcut.Description = 'DeepSeek Remote Bridge'
$shortcut.Save()
Write-Host "已加入开机启动：$link"