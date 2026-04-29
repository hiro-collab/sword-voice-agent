param(
    [string]$EnvPath = ".env",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8790,
    [string]$StatusDir = ".cache\sword_voice_agent",
    [string]$TtsStatusDir = "",
    [string]$InputGateUrl = "",
    [string]$DifyBaseUrl = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
Import-SwordEnv -EnvPath $EnvPath
Set-SwordAiTalkCoreWebTokenDefault | Out-Null
Set-SwordPythonPath
$aiTalkCoreRoot = Assert-EnvPath -Name "AI_TALK_CORE_ROOT"

if ([string]::IsNullOrWhiteSpace($InputGateUrl)) {
    $InputGateUrl = [Environment]::GetEnvironmentVariable(
        "AI_TALK_CORE_INPUT_GATE_URL",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($DifyBaseUrl)) {
    $DifyBaseUrl = [Environment]::GetEnvironmentVariable("DIFY_BASE_URL", "Process")
}
if ([string]::IsNullOrWhiteSpace($TtsStatusDir)) {
    $TtsStatusDir = [Environment]::GetEnvironmentVariable("TTS_OUTPUT_STATUS_DIR", "Process")
}
if ([string]::IsNullOrWhiteSpace($TtsStatusDir)) {
    $TtsStatusDir = ".cache\tts_service"
}

$command = @(
    "python",
    "-m",
    "sword_voice_agent.apps.console_server",
    "--host",
    $HostName,
    "--port",
    [string]$Port,
    "--ai-talk-core-root",
    $aiTalkCoreRoot,
    "--status-dir",
    (Resolve-SwordPath -Path $StatusDir),
    "--tts-status-dir",
    (Resolve-SwordPath -Path $TtsStatusDir)
)

if (-not [string]::IsNullOrWhiteSpace($InputGateUrl)) {
    $command += @("--input-gate-url", $InputGateUrl)
}
if (-not [string]::IsNullOrWhiteSpace($DifyBaseUrl)) {
    $command += @("--dify-base-url", $DifyBaseUrl)
}

Invoke-WithModuleStatus `
    -WorkingDirectory $repoRoot `
    -Command $command `
    -StatusDir $StatusDir `
    -ModuleName "console" `
    -ModuleLabel "Integration console" `
    -Detail "http://$HostName`:$Port" `
    -DryRun:$DryRun
