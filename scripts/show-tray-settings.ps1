# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
# Open the Windows setting page where tray icons are shown/hidden, and print
# the exact steps. Windows puts new notification-area icons in the "^" overflow
# by default; this is the supported place to choose otherwise.
$root = (Resolve-Path (Join-Path $PSScriptRoot '..')).Path
Write-Host '在 Windows 上把这个桥的托盘图标固定到任务栏：'
Write-Host '  1. 点任务栏右下角的 ^（显示隐藏的图标），找到绿色 D 图标；'
Write-Host '  2. 按住它拖到任务栏上，或右键任务栏 → 任务栏设置 → 选择任务栏上显示的图标，把 DeepSeek 打开；'
Write-Host '  3. 全屏游戏/视频会遮住任务栏，退出全屏后再看。'
Write-Host ''
Write-Host '正在打开任务栏设置…'
Start-Process 'ms-settings:taskbar'
