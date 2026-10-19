<#
.SYNOPSIS
  One-command setup for J.A.R.V.I.S. on a fresh Windows machine.

.DESCRIPTION
  Run from the repository folder:
      powershell -ExecutionPolicy Bypass -File scripts\setup.ps1

  Steps (each is skipped if already done, so re-running is safe):
    1. checks Python 3.11+ and Node.js 20+ are installed;
    2. creates .venv and installs the backend (pip, from PyPI);
    3. downloads the speech models once (pinned URLs, SHA-256 checked, resumable);
    4. installs the HUD's npm packages (exact versions from package-lock.json) and builds it;
    5. creates .env from .env.example if it doesn't exist yet.

  Network use: PyPI, the npm registry, the Electron binary from GitHub, and the model
  files from Hugging Face / GitHub. All one-time. Jarvis itself then talks only to
  the Anthropic API.

.PARAMETER SkipModels
  Don't download speech models (text-only use, or you'll copy models\ over yourself).
#>
param([switch]$SkipModels)

$ErrorActionPreference = 'Stop'
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root

function Step($text) { Write-Host "`n==> $text" -ForegroundColor Cyan }
function Fail($text) { Write-Host "Setup stopped: $text" -ForegroundColor Red; exit 1 }

Step 'Checking Python and Node.js'
$python = Get-Command py -ErrorAction SilentlyContinue
$pythonArgs = @('-3')
if (-not $python) { $python = Get-Command python -ErrorAction SilentlyContinue; $pythonArgs = @() }
if (-not $python) { Fail 'Python 3.11+ is not installed. Get it from https://www.python.org/downloads/ (tick "Add to PATH").' }
$version = & $python.Source @pythonArgs -c 'import sys; print("%d.%d" % sys.version_info[:2])'
if ([version]$version -lt [version]'3.11') { Fail "Python $version found; 3.11 or newer is required." }
Write-Host "Python $version"
$node = Get-Command node -ErrorAction SilentlyContinue
if (-not $node) { Fail 'Node.js is not installed. Get the LTS version from https://nodejs.org/.' }
$nodeVersion = (& node --version).TrimStart('v')
if ([version]$nodeVersion -lt [version]'20.0') { Fail "Node.js $nodeVersion found; 20 or newer is required." }
Write-Host "Node.js $nodeVersion"

Step 'Python environment (.venv) and backend packages'
if (-not (Test-Path '.venv\Scripts\python.exe')) { & $python.Source @pythonArgs -m venv .venv }
$venvPython = Join-Path $Root '.venv\Scripts\python.exe'
& $venvPython -m pip install --upgrade pip --quiet
& $venvPython -m pip install -e '.[dev]' --quiet
if ($LASTEXITCODE -ne 0) { Fail 'pip install failed (see the messages above).' }

if (-not $SkipModels) {
  Step 'Speech models (one-time download, about 1.2 GB; resumes if interrupted)'
  & $venvPython scripts\download_models.py
  if ($LASTEXITCODE -ne 0) { Fail 'Model download failed. Re-run this script to resume.' }
}

Step 'HUD (Electron + Three.js)'
Push-Location ui
try {
  & npm ci --no-fund --no-audit
  if ($LASTEXITCODE -ne 0) { Fail 'npm ci failed (see the messages above).' }
  & npm run build
  if ($LASTEXITCODE -ne 0) { Fail 'HUD build failed.' }
} finally { Pop-Location }

Step 'API key file'
if (-not (Test-Path '.env')) {
  Copy-Item '.env.example' '.env'
  Write-Host 'Created .env. Open it and paste your Anthropic API key (it never leaves this machine except to Anthropic).' -ForegroundColor Yellow
} else {
  Write-Host '.env already exists.'
}

Step 'Done'
Write-Host 'Start the HUD:        cd ui; npm start'
Write-Host 'Terminal voice mode:  .venv\Scripts\python -m jarvis --voice'
Write-Host 'Text mode:            .venv\Scripts\python -m jarvis'
