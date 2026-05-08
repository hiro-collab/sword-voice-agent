$ErrorActionPreference = "Stop"
$Target = Join-Path $PSScriptRoot "..\..\ops\scripts\home-control-stack\install-root-shortcuts.ps1"
& $Target @args
if ($LASTEXITCODE -is [int]) {
    exit $LASTEXITCODE
}
