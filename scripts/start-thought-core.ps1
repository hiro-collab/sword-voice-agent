param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 18787,
    [string]$StatusDir = ".cache\sword_voice_agent",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
$thoughtCorePath = Join-Path $repoRoot "services\thought-core"
if (-not (Test-Path -LiteralPath $thoughtCorePath -PathType Container)) {
    throw "thought-core service directory not found: $thoughtCorePath"
}

$env:PYTHONPATH = $thoughtCorePath
$command = @(
    "uv",
    "run",
    "python",
    "-m",
    "thought_core",
    "--host",
    $HostName,
    "--port",
    [string]$Port
)

Invoke-WithModuleStatus `
    -WorkingDirectory $repoRoot `
    -Command $command `
    -StatusDir $StatusDir `
    -ModuleName "thought_core_api" `
    -ModuleLabel "thought-core API" `
    -Detail ("http://{0}:{1}" -f $HostName, $Port) `
    -DryRun:$DryRun
