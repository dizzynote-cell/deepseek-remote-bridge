# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Register (or remove) the watchdog task: every few minutes, and at sign-in,
# check this bridge's health and start it again when it is down.
#
#   .\scripts\install-watchdog.ps1                 # every 3 minutes + at sign-in
#   .\scripts\install-watchdog.ps1 -Minutes 5
#   .\scripts\install-watchdog.ps1 -Remove
#
# The task runs as the current user in the background; it only touches the
# process recorded by this repository.

param(
  [int]$Minutes = 3,
  [switch]$Remove
)

$ErrorActionPreference = 'Stop'
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
$script = Join-Path $root 'scripts\watchdog-once.ps1'
$taskName = 'DeepSeek Remote Bridge Watchdog'

if ($Remove) {
  Unregister-ScheduledTask -TaskName $taskName -Confirm:$false -ErrorAction SilentlyContinue
  Write-Host "Removed scheduled task: $taskName"
  return
}

if (-not (Test-Path $script)) { throw "Missing $script" }
if ($Minutes -lt 1 -or $Minutes -gt 60) { throw 'Minutes must be between 1 and 60.' }

$action = New-ScheduledTaskAction -Execute "$env:SystemRoot\System32\WindowsPowerShell\v1.0\powershell.exe" `
  -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$script`"" `
  -WorkingDirectory $root
$triggers = @()
$triggers += New-ScheduledTaskTrigger -AtLogOn
$triggers += New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
  -RepetitionInterval (New-TimeSpan -Minutes $Minutes) `
  -RepetitionDuration (New-TimeSpan -Days 3650)
$settings = New-ScheduledTaskSettingsSet `
  -AllowStartIfOnBatteries `
  -DontStopIfGoingOnBatteries `
  -StartWhenAvailable `
  -MultipleInstances IgnoreNew `
  -ExecutionTimeLimit (New-TimeSpan -Minutes 5) `
  -Hidden

Register-ScheduledTask -TaskName $taskName -Action $action -Trigger $triggers -Settings $settings `
  -Description 'Starts the DeepSeek Remote Bridge again when its health check fails.' `
  -Force | Out-Null

Write-Host "Registered scheduled task: $taskName (every $Minutes min, and at sign-in)"
Write-Host "Check it with:  schtasks /Query /TN `"$taskName`" /V /FO LIST"
Write-Host "Remove it with: .\scripts\install-watchdog.ps1 -Remove"
