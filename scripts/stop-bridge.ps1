# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Stop only the bridge recorded in state/bridge.pid.
. (Join-Path $PSScriptRoot 'bridge-common.ps1')
$root = Get-BridgeRoot
Stop-Bridge -Root $root