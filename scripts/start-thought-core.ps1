param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 18787,
    [string]$StatusDir = ".cache\sword_voice_agent",
    [string]$EnvPath = ".env",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
$resolvedEnvPath = Resolve-SwordPath -Path $EnvPath
if (Test-Path -LiteralPath $resolvedEnvPath -PathType Leaf) {
    Import-SwordEnv -EnvPath $resolvedEnvPath
}
if ([string]::IsNullOrWhiteSpace($env:THOUGHT_CORE_PERSONA) -and [string]::IsNullOrWhiteSpace($env:SWORD_THOUGHT_CORE_PERSONA)) {
    $env:THOUGHT_CORE_PERSONA = "cheerful_ossan"
}
$thoughtCorePath = Join-Path $repoRoot "services\thought-core"
if (-not (Test-Path -LiteralPath $thoughtCorePath -PathType Container)) {
    throw "thought-core service directory not found: $thoughtCorePath"
}
$thoughtCoreSrcPath = Join-Path $thoughtCorePath "src"
if (-not (Test-Path -LiteralPath $thoughtCoreSrcPath -PathType Container)) {
    throw "thought-core src directory not found: $thoughtCoreSrcPath"
}

$env:PYTHONPATH = $thoughtCoreSrcPath
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
