param(
    [string]$EnvPath = ".env",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8765,
    [string]$InputGateUrl = "",
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

$resolvedStatusDir = Resolve-SwordPath -Path $StatusDir
$command = @(
    "python",
    "-m",
    "sword_voice_agent.apps.gesture_udp_receiver",
    "--host",
    $HostName,
    "--port",
    [string]$Port,
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
    -Detail "$HostName`:$Port" `
    -DryRun:$DryRun
