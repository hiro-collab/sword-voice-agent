param(
    [string]$EnvPath = ".env",
    [Alias("Input")]
    [string]$TopologyInput = "",
    [string]$Runtime = "",
    [ValidateSet("auto", "generic", "sword-events")]
    [string]$RuntimeAdapter = "sword-events",
    [string]$TurnId = "",
    [ValidateSet("overview", "tour", "trace", "debug", "cost", "security")]
    [string]$Mode = "trace",
    [ValidateSet("simple", "normal", "deep")]
    [string]$DetailLevel = "deep",
    [ValidateSet("ja", "en")]
    [string]$Language = "ja",
    [string]$OutDir = "out\sword-trace",
    [string]$RuntimeStatusFile = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

Import-SwordEnv -EnvPath $EnvPath
$rendererRootValue = [Environment]::GetEnvironmentVariable(
    "SYSTEM_HOUSE_RENDERER_ROOT",
    "Process"
)
if ([string]::IsNullOrWhiteSpace($rendererRootValue)) {
    $rendererRootValue = "..\organs\diagnostics\system-house-renderer"
}
$rendererRoot = Resolve-SwordPath -Path $rendererRootValue
if (-not (Test-Path -LiteralPath $rendererRoot)) {
    throw "SYSTEM_HOUSE_RENDERER_ROOT path not found: $rendererRoot"
}
$rendererRoot = (Resolve-Path -LiteralPath $rendererRoot).Path

if (
    -not [string]::IsNullOrWhiteSpace($TopologyInput) -and
    [string]::IsNullOrWhiteSpace($Runtime) -and
    -not $PSBoundParameters.ContainsKey("Mode")
) {
    $Mode = "overview"
}

if (
    [string]::IsNullOrWhiteSpace($TopologyInput) -and
    [string]::IsNullOrWhiteSpace($Runtime)
) {
    $Runtime = "http://127.0.0.1:8790/api/events?once=1"
}
if ([string]::IsNullOrWhiteSpace($RuntimeStatusFile)) {
    $RuntimeStatusFile = [Environment]::GetEnvironmentVariable(
        "SYSTEM_HOUSE_RENDERER_RUNTIME_STATUS_FILE",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($RuntimeStatusFile)) {
    $RuntimeStatusFile = ".cache\sword_voice_agent\runtime\system_house_renderer.json"
}
$resolvedRuntimeStatusFile = Resolve-SwordPath -Path $RuntimeStatusFile

$env:PYTHONPATH = Join-Path $rendererRoot "src"
$command = @(
    "python",
    "-m",
    "system_house_renderer",
    "map",
    "--mode",
    $Mode,
    "--detail-level",
    $DetailLevel,
    "--language",
    $Language,
    "--out",
    (Resolve-SwordPath -Path $OutDir),
    "--runtime-status-file",
    $resolvedRuntimeStatusFile
)

if (-not [string]::IsNullOrWhiteSpace($TopologyInput)) {
    $command += @("--input", (Resolve-SwordPath -Path $TopologyInput))
}

if (-not [string]::IsNullOrWhiteSpace($Runtime)) {
    $command += @(
        "--runtime",
        $Runtime,
        "--runtime-adapter",
        $RuntimeAdapter
    )
}

if (-not [string]::IsNullOrWhiteSpace($TurnId)) {
    $command += @("--turn-id", $TurnId)
}

Invoke-OrPrint `
    -WorkingDirectory $rendererRoot `
    -Command $command `
    -DryRun:$DryRun
