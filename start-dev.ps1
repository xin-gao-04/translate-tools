$ErrorActionPreference = 'Stop'

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
$frontendDir = Join-Path $repoRoot 'frontend'
$venvPython = Join-Path $repoRoot '.venv\Scripts\python.exe'
$venvScripts = Join-Path $repoRoot '.venv\Scripts'
$viteCmd = Join-Path $frontendDir 'node_modules\.bin\vite.cmd'
$electronExe = Join-Path $frontendDir 'node_modules\electron\dist\electron.exe'
$viteStdOutLog = Join-Path $frontendDir 'vite-dev.log'
$viteStdErrLog = Join-Path $frontendDir 'vite-dev.err.log'
$startedVite = $false

function Test-HttpReady {
    param(
        [Parameter(Mandatory = $true)]
        [string] $Url,
        [int] $Retries = 40,
        [int] $DelayMs = 500
    )

    for ($i = 0; $i -lt $Retries; $i++) {
        try {
            $response = Invoke-WebRequest -UseBasicParsing -Uri $Url -TimeoutSec 3
            if ($response.StatusCode -lt 500) {
                return $true
            }
        } catch {
        }
        Start-Sleep -Milliseconds $DelayMs
    }

    return $false
}

if (-not (Test-Path $venvPython)) {
    throw "Missing virtualenv Python: $venvPython"
}

if (-not (Test-Path $viteCmd)) {
    throw "Missing Vite launcher: $viteCmd. Run npm install in frontend first."
}

if (-not (Test-Path $electronExe)) {
    throw "Missing Electron executable: $electronExe. Run npm install in frontend first."
}

$env:Path = "$venvScripts;$env:Path"
$env:PYTHONUNBUFFERED = '1'
$env:ELECTRON_CUSTOM_DIR = 'v33.4.11'
$env:ELECTRON_MIRROR = 'https://github.com/electron/electron/releases/download/'

Write-Host 'Starting Vite dev server...' -ForegroundColor Cyan
if (Test-HttpReady -Url 'http://127.0.0.1:5173' -Retries 2 -DelayMs 200) {
    Write-Host 'Vite is already running at http://127.0.0.1:5173, reusing it.' -ForegroundColor Yellow
    $viteProcess = $null
} else {
    $viteProcess = Start-Process -FilePath $viteCmd `
        -WorkingDirectory $frontendDir `
        -RedirectStandardOutput $viteStdOutLog `
        -RedirectStandardError $viteStdErrLog `
        -PassThru
    $startedVite = $true
}

try {
    if (-not (Test-HttpReady -Url 'http://127.0.0.1:5173')) {
        throw "Vite did not become ready in time. Check logs: $viteStdOutLog, $viteStdErrLog"
    }

    if (Test-HttpReady -Url 'http://127.0.0.1:8765/api/health' -Retries 2 -DelayMs 200) {
        Write-Host 'Backend is already running at http://127.0.0.1:8765, Electron will reuse it.' -ForegroundColor Yellow
    }

    Write-Host 'Starting Electron UI...' -ForegroundColor Cyan
    $electronProcess = Start-Process -FilePath $electronExe `
        -ArgumentList 'electron/main.cjs', '--dev' `
        -WorkingDirectory $frontendDir `
        -PassThru

    $electronProcess.WaitForExit()
}
finally {
    if ($startedVite -and $viteProcess -and -not $viteProcess.HasExited) {
        Stop-Process -Id $viteProcess.Id -Force
    }
}
