@echo off
setlocal
rem SPDX-License-Identifier: MIT - Project: DeepSeek Remote Bridge
rem Ask before starting; a second run just opens the page.
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0scripts\start-bridge.ps1"
if errorlevel 1 (
  echo.
  echo Bridge start failed. Check state\logs\bridge.err.log
  pause
  exit /b 1
)
for /f "tokens=1,* delims==" %%A in ('findstr /b /c:"DSH_BRIDGE_PORT=" "%~dp0config\.env" 2^>nul') do set "DSH_PORT=%%B"
if "%DSH_PORT%"=="" set "DSH_PORT=8766"
start "" "http://127.0.0.1:%DSH_PORT%/"
exit /b 0