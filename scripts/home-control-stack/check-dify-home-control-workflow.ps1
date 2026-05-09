$ErrorActionPreference = "Stop"
$Target = Join-Path $PSScriptRoot "..\..\ops\scripts\home-control-stack\check-dify-home-control-workflow.ps1"
& $Target @args
if ($LASTEXITCODE -is [int]) {
    exit $LASTEXITCODE
}
