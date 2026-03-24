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
$appPackageJson = Join-Path $appRoot "package.json"
$portableReadme = Join-Path $outputRoot "README-portable.txt"
$portableLauncher = Join-Path $outputRoot "start-translate-comments.bat"
$backendBuildScript = Join-Path $scriptRoot "build-python-backend.ps1"
$backendBuiltRoot = Join-Path $repoRoot "release\backend-exe\translate-comments-backend"

if (-not (Test-Path $viteCli)) {
  throw "Vite CLI not found: $viteCli"
}

if (-not (Test-Path $backendBuildScript)) {
  throw "Backend build script not found: $backendBuildScript"
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

& powershell -ExecutionPolicy Bypass -File $backendBuildScript

if (-not (Test-Path $backendBuiltRoot)) {
  throw "Bundled backend not found after build: $backendBuiltRoot"
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

Copy-Item -Force (Join-Path $frontendRoot "package.json") $appPackageJson
Copy-Item -Recurse -Force (Join-Path $frontendRoot "dist") $appRoot
Copy-Item -Recurse -Force (Join-Path $frontendRoot "electron") $appRoot
Copy-Item -Recurse -Force (Join-Path $backendBuiltRoot "*") $backendRoot

Get-ChildItem -Path $backendRoot -Recurse -Directory -Filter "__pycache__" | Remove-Item -Recurse -Force
Get-ChildItem -Path $backendRoot -Recurse -File -Include "*.pyc", "*.pyo" | Remove-Item -Force

@" 
translate-comments portable package

Run:
  .\start-translate-comments.bat
  or
  .\translate-comments.exe

Runtime notes:
  - start-translate-comments.bat starts the backend first, waits for health,
    and then opens the Electron frontend.
  - The packaged frontend can also launch .\resources\backend\translate-comments-backend.exe itself.
  - If the backend executable is missing or cannot start, the app falls back
    to Python-based startup when TRANSLATE_COMMENTS_PYTHON is set.
"@ | Set-Content -Encoding ASCII $portableReadme

@'
@echo off
setlocal
cd /d "%~dp0"

set "BACKEND_EXE=%~dp0resources\backend\translate-comments-backend.exe"
set "FRONTEND_EXE=%~dp0translate-comments.exe"
set "HEALTH_URL=http://127.0.0.1:8765/api/health"

if not exist "%BACKEND_EXE%" (
  echo Backend executable not found: "%BACKEND_EXE%"
  pause
  exit /b 1
)

if not exist "%FRONTEND_EXE%" (
  echo Frontend executable not found: "%FRONTEND_EXE%"
  pause
  exit /b 1
)

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$ProgressPreference='SilentlyContinue';" ^
  "try { Invoke-WebRequest -UseBasicParsing '%HEALTH_URL%' ^| Out-Null; exit 0 } catch { exit 1 }"
if errorlevel 1 (
  start "" "%BACKEND_EXE%" --port 8765
  powershell -NoProfile -ExecutionPolicy Bypass -Command ^
    "$ProgressPreference='SilentlyContinue';" ^
    "$deadline=(Get-Date).AddSeconds(20);" ^
    "while((Get-Date) -lt $deadline) { try { Invoke-WebRequest -UseBasicParsing '%HEALTH_URL%' ^| Out-Null; exit 0 } catch { Start-Sleep -Milliseconds 500 } }" ^
    "Write-Host 'Backend did not become ready on port 8765 in time.';" ^
    "exit 1"
  if errorlevel 1 (
    echo Backend startup timed out.
    pause
    exit /b 1
  )
)

start "" "%FRONTEND_EXE%"
exit /b 0
'@ | Set-Content -Encoding ASCII $portableLauncher
