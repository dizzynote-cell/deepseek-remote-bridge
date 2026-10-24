# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Restart the bridge without touching any other process.
. (Join-Path $PSScriptRoot 'bridge-common.ps1')
$root = Get-BridgeRoot
Stop-Bridge -Root $root
Start-Sleep -Seconds 1
Start-Bridge -Root $root