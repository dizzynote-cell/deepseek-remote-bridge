# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Show the bridge in the Windows notification area (system tray).
. (Join-Path $PSScriptRoot 'bridge-common.ps1')
$root = Get-BridgeRoot
Start-Tray -Root $root