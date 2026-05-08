$ErrorActionPreference = "Stop"
$Target = Join-Path $PSScriptRoot "..\..\ops\scripts\home-control-stack\check-environment-state-server.ps1"
& $Target @args
if ($LASTEXITCODE -is [int]) {
    exit $LASTEXITCODE
}
