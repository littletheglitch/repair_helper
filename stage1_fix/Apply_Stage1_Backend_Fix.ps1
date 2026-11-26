$ErrorActionPreference = 'Stop'
$scriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$patcher = Join-Path $scriptDir 'apply_stage1_backend_fix.py'

if (-not (Test-Path $patcher)) {
    throw "Patch file not found: $patcher"
}

$python = Get-Command py -ErrorAction SilentlyContinue
if ($python) {
    & py -3 $patcher (Get-Location).Path
} else {
    $python = Get-Command python -ErrorAction SilentlyContinue
    if (-not $python) {
        throw 'Python 3 was not found. Install/use the same Python that runs ElectronicsDiagnostic, then rerun this script.'
    }
    & python $patcher (Get-Location).Path
}

if ($LASTEXITCODE -ne 0) {
    throw "Patch application failed with exit code $LASTEXITCODE. No partial changes should remain; check the backup files if needed."
}
