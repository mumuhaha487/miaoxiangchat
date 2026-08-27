$ErrorActionPreference = 'Stop'

$projectRoot = Split-Path -Parent $PSScriptRoot
$python = Join-Path $projectRoot 'windows-client\.venv\Scripts\python.exe'
$output = Join-Path $projectRoot 'artifacts\windows'
$work = Join-Path $PSScriptRoot 'build'
$spec = Join-Path $PSScriptRoot 'spec'

if (-not (Test-Path -LiteralPath $python)) {
    throw "Windows client Python environment was not found: $python"
}

New-Item -ItemType Directory -Force -Path $output, $work, $spec | Out-Null
& $python -m PyInstaller `
    --noconfirm `
    --clean `
    --onefile `
    --windowed `
    --name MiaoxiangVipActivationManager-x64 `
    --distpath $output `
    --workpath $work `
    --specpath $spec `
    (Join-Path $PSScriptRoot 'main.py')

if ($LASTEXITCODE -ne 0) {
    throw "PyInstaller failed with exit code $LASTEXITCODE"
}

Get-FileHash -Algorithm SHA256 (Join-Path $output 'MiaoxiangVipActivationManager-x64.exe')
