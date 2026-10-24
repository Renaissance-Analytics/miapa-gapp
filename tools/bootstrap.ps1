# Locked preparation only; the first test stage is alignment in tools.ci.
$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Test-Path .venv/Scripts/python.exe)) {
    python -m venv .venv
    if ($LASTEXITCODE -ne 0) { throw 'Virtual environment prerequisite failed' }
}
& .venv/Scripts/python.exe -m pip --isolated install --require-hashes --only-binary=:all: --disable-pip-version-check -r requirements.txt
if ($LASTEXITCODE -ne 0) { throw 'Locked Python installation failed' }
$maipaConfigRoot = Join-Path (Get-Location) 'artifacts/bootstrap'
New-Item -ItemType Directory -Path $maipaConfigRoot -Force | Out-Null
$maipaUserConfig = Join-Path $maipaConfigRoot 'user.npmrc'
$maipaGlobalConfig = Join-Path $maipaConfigRoot 'global.npmrc'
[System.IO.File]::WriteAllText($maipaUserConfig, '')
[System.IO.File]::WriteAllText($maipaGlobalConfig, '')
foreach ($directory in @('frontend', 'backend/agent')) {
    Push-Location $directory
    try {
        npm.cmd ci --ignore-scripts --no-audit --no-fund --userconfig=$maipaUserConfig --globalconfig=$maipaGlobalConfig --registry=https://registry.npmjs.org/
        if ($LASTEXITCODE -ne 0) { throw 'Locked npm installation failed' }
    } finally { Pop-Location }
}
