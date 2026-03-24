Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendRoot = Split-Path -Parent $scriptRoot
$repoRoot = Split-Path -Parent $frontendRoot
$venvPython = Join-Path $repoRoot ".venv\Scripts\python.exe"
$entryScript = Join-Path $scriptRoot "backend_entry.py"
$outputRoot = Join-Path $repoRoot "release\backend-exe"
$workRoot = Join-Path $repoRoot "build\pyinstaller-backend"
$specRoot = Join-Path $repoRoot "build\pyinstaller-spec"

if (-not (Test-Path $venvPython)) {
  throw "Missing virtualenv Python: $venvPython"
}

if (-not (Test-Path $entryScript)) {
  throw "Missing backend entry script: $entryScript"
}

Get-Process | Where-Object {
  $_.Path -and $_.Path -like (Join-Path $outputRoot "*")
} | Stop-Process -Force -ErrorAction SilentlyContinue

try {
  & $venvPython -m PyInstaller --version | Out-Null
} catch {
  throw "PyInstaller is not installed in .venv. Run: .\.venv\Scripts\python.exe -m pip install pyinstaller"
}

if (Test-Path $outputRoot) {
  Remove-Item -Recurse -Force $outputRoot
}
if (Test-Path $workRoot) {
  Remove-Item -Recurse -Force $workRoot
}
if (Test-Path $specRoot) {
  Remove-Item -Recurse -Force $specRoot
}

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
New-Item -ItemType Directory -Force -Path $workRoot | Out-Null
New-Item -ItemType Directory -Force -Path $specRoot | Out-Null

& $venvPython -m PyInstaller `
  --noconfirm `
  --clean `
  --onedir `
  --name "translate-comments-backend" `
  --distpath $outputRoot `
  --workpath $workRoot `
  --specpath $specRoot `
  --paths $repoRoot `
  --collect-submodules uvicorn `
  --collect-submodules websockets `
  --collect-submodules watchfiles `
  --hidden-import httptools `
  --hidden-import anyio._backends._asyncio `
  $entryScript
