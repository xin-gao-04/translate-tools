Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendRoot = Split-Path -Parent $scriptRoot
$repoRoot = Split-Path -Parent $frontendRoot
$outputRoot = Join-Path $repoRoot "release\portable-win"
$electronDist = Join-Path $frontendRoot "node_modules\electron\dist"
$electronInstallScript = Join-Path $frontendRoot "node_modules\electron\install.js"
$viteCli = Join-Path $frontendRoot "node_modules\vite\bin\vite.js"
$appRoot = Join-Path $outputRoot "resources\app"
$backendRoot = Join-Path $outputRoot "resources\backend"
$backendRuntimeRoot = Join-Path $outputRoot "resources\backend-runtime"
$appPackageJson = Join-Path $appRoot "package.json"
$portableReadme = Join-Path $outputRoot "README-portable.txt"
$venvRoot = Join-Path $repoRoot ".venv"

if (-not (Test-Path $viteCli)) {
  throw "Vite CLI not found: $viteCli"
}

if (-not (Test-Path $electronDist)) {
  if (-not (Test-Path $electronInstallScript)) {
    throw "Electron installer not found: $electronInstallScript"
  }

  $originalElectronCustomDir = $env:ELECTRON_CUSTOM_DIR
  $originalNpmElectronCustomDir = $env:npm_config_electron_custom_dir
  Push-Location $frontendRoot
  try {
    Remove-Item Env:ELECTRON_CUSTOM_DIR -ErrorAction SilentlyContinue
    Remove-Item Env:npm_config_electron_custom_dir -ErrorAction SilentlyContinue
    & node $electronInstallScript
  } finally {
    if ($null -ne $originalElectronCustomDir) {
      $env:ELECTRON_CUSTOM_DIR = $originalElectronCustomDir
    } else {
      Remove-Item Env:ELECTRON_CUSTOM_DIR -ErrorAction SilentlyContinue
    }

    if ($null -ne $originalNpmElectronCustomDir) {
      $env:npm_config_electron_custom_dir = $originalNpmElectronCustomDir
    } else {
      Remove-Item Env:npm_config_electron_custom_dir -ErrorAction SilentlyContinue
    }
    Pop-Location
  }
}

if (-not (Test-Path $electronDist)) {
  throw "Electron runtime not found after install: $electronDist"
}

Push-Location $frontendRoot
try {
  & node $viteCli build
} finally {
  Pop-Location
}

Get-Process | Where-Object {
  $_.Path -and (
    $_.Path -eq (Join-Path $outputRoot "translate-comments.exe") -or
    $_.Path -like (Join-Path $outputRoot "*")
  )
} | Stop-Process -Force

if (Test-Path $outputRoot) {
  Remove-Item -Recurse -Force $outputRoot
}

New-Item -ItemType Directory -Force -Path $outputRoot | Out-Null
Copy-Item -Recurse -Force (Join-Path $electronDist "*") $outputRoot

if (Test-Path (Join-Path $outputRoot "electron.exe")) {
  Rename-Item -Path (Join-Path $outputRoot "electron.exe") -NewName "translate-comments.exe"
}

if (Test-Path (Join-Path $outputRoot "resources\default_app.asar")) {
  Remove-Item -Force (Join-Path $outputRoot "resources\default_app.asar")
}

New-Item -ItemType Directory -Force -Path $appRoot | Out-Null
New-Item -ItemType Directory -Force -Path $backendRoot | Out-Null
if (Test-Path $venvRoot) {
  New-Item -ItemType Directory -Force -Path $backendRuntimeRoot | Out-Null
}

Copy-Item -Force (Join-Path $frontendRoot "package.json") $appPackageJson
Copy-Item -Recurse -Force (Join-Path $frontendRoot "dist") $appRoot
Copy-Item -Recurse -Force (Join-Path $frontendRoot "electron") $appRoot
Copy-Item -Recurse -Force (Join-Path $repoRoot "translate_comments") $backendRoot
Copy-Item -Force (Join-Path $repoRoot "requirements.txt") $backendRoot
if (Test-Path $venvRoot) {
  Copy-Item -Recurse -Force (Join-Path $venvRoot "*") $backendRuntimeRoot
}

Get-ChildItem -Path $backendRoot -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem -Path $backendRoot -Recurse -File -Include "*.pyc", "*.pyo" | Remove-Item -Force
if (Test-Path $backendRuntimeRoot) {
  Get-ChildItem -Path $backendRuntimeRoot -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
}

@"
translate-comments portable package

Run:
  .\translate-comments.exe

Runtime notes:
  - The packaged frontend will prefer .\resources\backend-runtime\Scripts\python.exe
    when present.
  - If the bundled runtime cannot be used on the target machine, set
    TRANSLATE_COMMENTS_PYTHON to a usable python.exe path before launch.
"@ | Set-Content -Encoding ASCII $portableReadme
