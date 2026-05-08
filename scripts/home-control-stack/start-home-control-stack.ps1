$ErrorActionPreference = "Stop"
$Target = Join-Path $PSScriptRoot "..\..\ops\scripts\system.ps1"
& $Target start @args
if ($LASTEXITCODE -is [int]) {
    exit $LASTEXITCODE
}
