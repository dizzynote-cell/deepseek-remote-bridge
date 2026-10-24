# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Backwards-compatible entry point: installs or removes the sign-in shortcut.
# Use scripts\install-shortcuts.ps1 directly for the desktop shortcut too.
param([switch]$Remove)
if ($Remove) {
  & (Join-Path $PSScriptRoot 'install-shortcuts.ps1') -Remove
} else {
  & (Join-Path $PSScriptRoot 'install-shortcuts.ps1') -Startup
}