# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Remove the notification-area icon (the bridge keeps running).
. (Join-Path $PSScriptRoot 'bridge-common.ps1')
$root = Get-BridgeRoot
Stop-Tray -Root $root
