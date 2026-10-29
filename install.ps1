# SPDX-License-Identifier: MIT
# Copyright (c) 2026 xiyannan — Project: DeepSeek Remote Bridge
#
# Interactive setup for a local install. It only writes config/.env inside this
# repository and never touches the Codex bridge, the DeepSeek Harness install,
# or any server.
#
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1
#   powershell -NoProfile -ExecutionPolicy Bypass -File .\install.ps1 -Unattended
#
# Options:
#   -DshExe <path>        DeepSeek Harness.exe (default: auto-detect)
#   -Port <number>        listen port (default 8766)
#   -ProjectsRoot <path>  folder for new sessions (default %USERPROFILE%\DSH-Bridge\projects)
#   -Preset <name>        workspace-write | danger-full-access
#   -VoiceEnvFile <path>  optional TTS credentials file (never copied into the repo)
#   -Force                overwrite an existing config/.env (a .bak is kept)
#   -NoStart              do not start the bridge at the end
#   -NoAutostart          do not create the sign-in shortcut or recovery task
#   -NoDesktopShortcut    do not create the desktop shortcut

param(
  [switch]$Unattended,
  [string]$DshExe = '',
  [int]$Port = 8766,
  [string]$ProjectsRoot = '',
  [ValidateSet('workspace-write', 'danger-full-access')]
  [string]$Preset = 'workspace-write',
  [string]$VoiceEnvFile = '',
  [switch]$Force,
  [switch]$NoStart,
  [switch]$NoAutostart,
  [switch]$NoDesktopShortcut
)

$ErrorActionPreference = 'Stop'
$root = $PSScriptRoot
try { [Console]::OutputEncoding = [System.Text.UTF8Encoding]::new() } catch { }

function Ask([string]$label, [string]$default = '') {
  if ($Unattended) { return $default }
  $suffix = if ($default) { " [$default]" } else { '' }
  $value = Read-Host "$label$suffix"
  if ([string]::IsNullOrWhiteSpace($value)) { return $default }
  return $value.Trim()
}

function AskYes([string]$label, [string]$default = 'N') {
  if ($Unattended) { return ($default -match '^[Yy]') }
  $value = Ask "$label (Y/N)" $default
  return ($value -match '^[Yy]')
}

function Write-Step([string]$text) { Write-Host "`n$text" -ForegroundColor Cyan }
function Write-Ok([string]$text) { Write-Host "  $text" -ForegroundColor Green }
function Write-Note([string]$text) { Write-Host "  $text" -ForegroundColor DarkGray }

Write-Host 'DeepSeek Remote Bridge - Setup' -ForegroundColor Cyan
Write-Host 'Turns your installed DeepSeek Harness into a page you can use from a phone or another PC.'
Write-Host 'This installer only writes config/.env inside this folder. It does not deploy anything.'
if (-not $Unattended) { Read-Host 'Press Enter to begin' | Out-Null }

# ---- 1. Python ------------------------------------------------------------
Write-Step '[1/6] Environment'
$python = Get-Command python -ErrorAction SilentlyContinue
if (-not $python) { throw 'Python 3.10+ was not found in PATH. Install Python first.' }
$version = (& $python.Source --version) -join ' '
Write-Ok "Python: $version ($($python.Source))"
$cliDefault = Join-Path $root 'bridge.py'
if (-not (Test-Path $cliDefault)) { throw "bridge.py not found next to install.ps1 ($root)." }

# ---- 2. DeepSeek Harness --------------------------------------------------
Write-Step '[2/6] DeepSeek Harness'
$candidates = New-Object System.Collections.Generic.List[string]
if ($DshExe) { $candidates.Add($DshExe) }
Get-Process -Name 'DeepSeek Harness' -ErrorAction SilentlyContinue |
  ForEach-Object { if ($_.Path) { $candidates.Add($_.Path) } }
@(
  'C:\Program Files\DSH\DeepSeek Harness.exe',
  'C:\Program Files\DeepSeek Harness\DeepSeek Harness.exe',
  (Join-Path $env:LOCALAPPDATA 'Programs\DSH\DeepSeek Harness.exe')
) | ForEach-Object { $candidates.Add($_) }

$exePath = ''
foreach ($candidate in $candidates) {
  if ($candidate -and (Test-Path -LiteralPath $candidate)) { $exePath = (Resolve-Path -LiteralPath $candidate).Path; break }
}
if (-not $exePath) {
  Write-Note 'Not found automatically. Paste the full path to DeepSeek Harness.exe.'
  $exePath = Ask 'DeepSeek Harness.exe'
  if (-not $exePath -or -not (Test-Path -LiteralPath $exePath)) { throw 'DeepSeek Harness.exe was not found. Install and sign in to the desktop app first.' }
}
$cliPath = Join-Path (Split-Path $exePath -Parent) 'resources\app.asar\dsh\node_modules\@deepseek-ai\dsh-desktop-host\lib\cli.js'
if (-not (Test-Path -LiteralPath (Join-Path (Split-Path $exePath -Parent) 'resources\app.asar'))) {
  throw "The harness package was not found next to $exePath (expected resources\app.asar)."
}
Write-Ok "Harness: $exePath"
Write-Note 'The bridge starts its own runtime from this install; the desktop window does not need to stay open.'

# ---- 3. Port --------------------------------------------------------------
Write-Step '[3/6] Local port'
if (-not $Unattended) { $Port = [int](Ask 'Listen port (the Codex bridge uses 8765, keep that free)' $Port) }
$busy = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
if ($busy) { throw "Port $Port is already in use. Pick another port or stop the process that owns it." }
Write-Ok "Will listen on http://127.0.0.1:$Port/"

# ---- 4. Workspaces --------------------------------------------------------
Write-Step '[4/6] Folders for new sessions'
if (-not $ProjectsRoot) { $ProjectsRoot = Join-Path $env:USERPROFILE 'DSH-Bridge\projects' }
if (-not $Unattended) { $ProjectsRoot = Ask 'Projects root (new sessions can only use folders under the allowed roots)' $ProjectsRoot }
$standaloneRoot = Join-Path (Split-Path $ProjectsRoot -Parent) 'sessions'
New-Item -ItemType Directory -Force -Path $ProjectsRoot, $standaloneRoot | Out-Null
Write-Ok "Projects: $ProjectsRoot"
Write-Ok "Standalone: $standaloneRoot"

# ---- 5. Permission preset + optional voice --------------------------------
Write-Step '[5/6] Default permission preset'
Write-Note 'workspace-write  = tools write inside the workspace; wider actions ask first'
Write-Note 'danger-full-access = full file access without approval prompts (like the desktop "full access")'
if (-not $Unattended) {
  $choice = Ask 'Enter 1 for workspace-write, 2 for danger-full-access' '1'
  $Preset = if ($choice -eq '2') { 'danger-full-access' } else { 'workspace-write' }
}
Write-Ok "Default preset: $Preset"

$voiceEnabled = '0'
$voicePath = ''
if (AskYes 'Configure optional read-aloud voice (needs a TTS credentials file)' 'N') {
  $voicePath = Ask 'Path to the TTS credentials file (VOLC_API_KEY / VOLC_VOICE_ID / X-Api-Resource-Id)' $VoiceEnvFile
  if ($voicePath -and (Test-Path -LiteralPath $voicePath)) {
    $voiceEnabled = '1'
    Write-Ok "Voice: $voicePath (kept outside this repository)"
  } else {
    Write-Note 'Voice file not found; voice stays off. You can enable it later in config/.env.'
    $voicePath = ''
  }
}

# ---- 6. Write config + start ---------------------------------------------
Write-Step '[6/6] Write configuration'
$configDir = Join-Path $root 'config'
New-Item -ItemType Directory -Force -Path $configDir | Out-Null
$configFile = Join-Path $configDir '.env'
$example = Join-Path $configDir '.env.example'
if ((Test-Path $configFile) -and -not $Force -and -not $Unattended) {
  if (-not (AskYes 'config/.env already exists; overwrite it (a .bak backup is kept)?' 'N')) {
    Write-Note 'Keeping the existing config/.env. Setup stopped before starting the bridge.'
    exit 0
  }
}
if (Test-Path $configFile) { Copy-Item $configFile "$configFile.bak" -Force }

$values = [ordered]@{
  'DSH_BRIDGE_HOST'                  = '127.0.0.1'
  'DSH_BRIDGE_PORT'                  = "$Port"
  'DSH_BRIDGE_BASE_PATH'             = '/'
  'DSH_EXE'                          = $exePath
  'DSH_CLI'                          = $cliPath
  'DSH_HOME'                         = ''
  'DSH_BRIDGE_PROVIDER'              = 'deepseek-account'
  'DSH_BRIDGE_MODEL'                 = 'deepseek-flash'
  'DSH_BRIDGE_REASONING_EFFORT'      = ''
  'DSH_BRIDGE_PERMISSION_PRESET'     = $Preset
  'DSH_BRIDGE_PROJECTS_ROOT'         = $ProjectsRoot
  'DSH_BRIDGE_STANDALONE_DIR'        = $standaloneRoot
  'DSH_BRIDGE_HOME'                  = (Join-Path $root 'state')
  'DSH_BRIDGE_VOICE'                 = $voiceEnabled
  'VOLC_TTS_ENV_FILE'                = $voicePath
}
$output = @(
  '# Generated by install.ps1 for this machine. Local only; git-ignored.',
  '# Re-run install.ps1 -Force to regenerate, or edit the values below by hand.'
)
foreach ($key in $values.Keys) { $output += "$key=$($values[$key])" }
$output | Set-Content -LiteralPath $configFile -Encoding UTF8
Write-Ok "Wrote $configFile"

if ((-not $NoStart) -and (AskYes 'Start the bridge now' 'Y')) {
  & (Join-Path $root 'scripts\start-bridge.ps1')
}
if ((-not $NoDesktopShortcut) -and (AskYes 'Create a desktop bridge shortcut' 'Y')) {
  & (Join-Path $root 'scripts\install-shortcuts.ps1') -Desktop | Out-Host
}
if ((-not $NoAutostart) -and (AskYes 'Start the bridge at Windows sign-in' 'Y')) {
  & (Join-Path $root 'scripts\install-shortcuts.ps1') -Startup | Out-Host
}
if ((-not $NoAutostart) -and (AskYes 'Register the auto-recovery task (restarts the bridge within 3 minutes if it dies)' 'Y')) {
  & (Join-Path $root 'scripts\install-watchdog.ps1') -Minutes 3 | Out-Host
}

Write-Host "`nSetup complete." -ForegroundColor Green
Write-Host "  Page      : http://127.0.0.1:$Port/"
Write-Host "  Tray      : green D in the notification area (right-click for start/stop/restart)"
Write-Host "  Phone     : same Wi-Fi + a reverse proxy or tunnel with HTTPS, then add it to the home screen"
Write-Host "  Next      : sign in to DeepSeek Harness once so the runtime has an account; the bridge never stores your password"
Write-Host "  Docs      : README.md (install, phone access, troubleshooting) and docs/ for the internals"
if (-not $voicePath) { Write-Host '  Voice     : off. Add DSH_BRIDGE_VOICE=1 and VOLC_TTS_ENV_FILE=<file> in config/.env to enable.' }
