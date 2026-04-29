param(
    [string]$EnvPath = ".env",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8765,
    [string]$InputGateUrl = "",
    [string]$MinConfidence = "",
    [string]$ActivationDelay = "",
    [string]$ReleaseDelay = "",
    [int]$DebugEvery = 30,
    [string]$StatusDir = ".cache\sword_voice_agent",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
Import-SwordEnv -EnvPath $EnvPath
Set-SwordAiTalkCoreWebTokenDefault | Out-Null
Set-SwordPythonPath

if ([string]::IsNullOrWhiteSpace($InputGateUrl)) {
    $InputGateUrl = [Environment]::GetEnvironmentVariable(
        "AI_TALK_CORE_INPUT_GATE_URL",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($MinConfidence)) {
    $MinConfidence = [Environment]::GetEnvironmentVariable(
        "SWORD_VOICE_AGENT_MIN_CONFIDENCE",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($ActivationDelay)) {
    $ActivationDelay = [Environment]::GetEnvironmentVariable(
        "SWORD_VOICE_AGENT_ACTIVATION_DELAY",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($ReleaseDelay)) {
    $ReleaseDelay = [Environment]::GetEnvironmentVariable(
        "SWORD_VOICE_AGENT_RELEASE_DELAY",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($MinConfidence)) {
    $MinConfidence = "0.8"
}
if ([string]::IsNullOrWhiteSpace($ActivationDelay)) {
    $ActivationDelay = "0.3"
}
if ([string]::IsNullOrWhiteSpace($ReleaseDelay)) {
    $ReleaseDelay = "0.5"
}

$resolvedStatusDir = Resolve-SwordPath -Path $StatusDir
$command = @(
    "python",
    "-m",
    "sword_voice_agent.apps.gesture_udp_receiver",
    "--host",
    $HostName,
    "--port",
    [string]$Port,
    "--min-confidence",
    $MinConfidence,
    "--activation-delay",
    $ActivationDelay,
    "--release-delay",
    $ReleaseDelay,
    "--debug",
    "--debug-every",
    [string]$DebugEvery,
    "--status-dir",
    $resolvedStatusDir
)

if (-not [string]::IsNullOrWhiteSpace($InputGateUrl)) {
    $command += @("--input-gate-url", $InputGateUrl)
}

Invoke-WithModuleStatus `
    -WorkingDirectory $repoRoot `
    -Command $command `
    -StatusDir $StatusDir `
    -ModuleName "gesture_udp_receiver" `
    -ModuleLabel "Gesture UDP receiver" `
    -Detail "$HostName`:$Port min_conf=$MinConfidence activation=$ActivationDelay release=$ReleaseDelay" `
    -DryRun:$DryRun
