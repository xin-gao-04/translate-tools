Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$scriptRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendRoot = Split-Path -Parent $scriptRoot
$repoRoot = Split-Path -Parent $frontendRoot
$outputRoot = Join-Path $repoRoot "release\portable-win"
$electronDist = Join-Path $frontendRoot "node_modules\electron\dist"
$appRoot = Join-Path $outputRoot "resources\app"
$backendRoot = Join-Path $outputRoot "resources\backend"
$backendRuntimeRoot = Join-Path $outputRoot "resources\backend-runtime"
$appPackageJson = Join-Path $appRoot "package.json"
$portableReadme = Join-Path $outputRoot "README-portable.txt"
$venvRoot = Join-Path $repoRoot ".venv"

if (-not (Test-Path $electronDist)) {
  throw "Electron runtime not found: $electronDist"
}

Push-Location $frontendRoot
try {
  & npm.cmd run build
} finally {
  Pop-Location
}

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
