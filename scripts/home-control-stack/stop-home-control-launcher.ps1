$ErrorActionPreference = "Stop"
$Target = Join-Path $PSScriptRoot "..\..\ops\scripts\home-control-stack\stop-home-control-launcher.ps1"
& $Target @args
if ($LASTEXITCODE -is [int]) {
    exit $LASTEXITCODE
}
