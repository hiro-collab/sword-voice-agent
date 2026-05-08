$ErrorActionPreference = "Stop"
$Target = Join-Path $PSScriptRoot "..\..\ops\scripts\system.ps1"
& $Target status @args
if ($LASTEXITCODE -is [int]) {
    exit $LASTEXITCODE
}
