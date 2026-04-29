param(
    [string]$EnvPath = ".env",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8790,
    [string]$StatusDir = ".cache\sword_voice_agent",
    [string]$TtsStatusDir = "",
    [string]$TtsAppVolumeFile = "",
    [string]$TtsVolumeUrl = "",
    [string]$InputGateUrl = "",
    [string]$DifyBaseUrl = "",
    [string]$AvatarUrl = "",
    [string]$AvatarModelUrl = "",
    [string]$TtsVolumePreviewUrl = "",
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
if ([string]::IsNullOrWhiteSpace($AvatarUrl)) {
    $AvatarUrl = [Environment]::GetEnvironmentVariable("AVATAR_SERVICE_URL", "Process")
}
if ([string]::IsNullOrWhiteSpace($AvatarModelUrl)) {
    $AvatarModelUrl = [Environment]::GetEnvironmentVariable("AVATAR_MODEL_URL", "Process")
}
if (-not [string]::IsNullOrWhiteSpace($AvatarUrl)) {
    $AvatarModelUrl = Resolve-SwordAvatarModelUrl -ModelUrl $AvatarModelUrl
}
if ([string]::IsNullOrWhiteSpace($TtsStatusDir)) {
    $TtsStatusDir = [Environment]::GetEnvironmentVariable("TTS_OUTPUT_STATUS_DIR", "Process")
}
if ([string]::IsNullOrWhiteSpace($TtsVolumeUrl)) {
    $TtsVolumeUrl = [Environment]::GetEnvironmentVariable("TTS_VOLUME_URL", "Process")
}
if ([string]::IsNullOrWhiteSpace($TtsVolumePreviewUrl)) {
    $TtsVolumePreviewUrl = [Environment]::GetEnvironmentVariable(
        "TTS_VOLUME_PREVIEW_URL",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($TtsVolumePreviewUrl) -and
    -not [string]::IsNullOrWhiteSpace($TtsVolumeUrl)) {
    $TtsVolumePreviewUrl = "$($TtsVolumeUrl.TrimEnd('/'))/preview"
}
if ([string]::IsNullOrWhiteSpace($TtsAppVolumeFile)) {
    $TtsAppVolumeFile = [Environment]::GetEnvironmentVariable(
        "TTS_SERVICE_APP_VOLUME_FILE",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($TtsStatusDir)) {
    $TtsStatusDir = ".cache\tts_service"
}
if ([string]::IsNullOrWhiteSpace($TtsAppVolumeFile)) {
    $TtsAppVolumeFile = Join-Path (Resolve-SwordPath -Path $TtsStatusDir) "app_volume.json"
}
if (-not $DryRun) {
    Assert-SwordPortsAvailable -TcpPorts @($Port)
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
    (Resolve-SwordPath -Path $TtsStatusDir),
    "--tts-app-volume-file",
    (Resolve-SwordPath -Path $TtsAppVolumeFile)
)

if (-not [string]::IsNullOrWhiteSpace($InputGateUrl)) {
    $command += @("--input-gate-url", $InputGateUrl)
}
if (-not [string]::IsNullOrWhiteSpace($DifyBaseUrl)) {
    $command += @("--dify-base-url", $DifyBaseUrl)
}
if (-not [string]::IsNullOrWhiteSpace($TtsVolumeUrl)) {
    $command += @("--tts-volume-url", $TtsVolumeUrl)
}
if (-not [string]::IsNullOrWhiteSpace($TtsVolumePreviewUrl)) {
    $command += @("--tts-volume-preview-url", $TtsVolumePreviewUrl)
}
if (-not [string]::IsNullOrWhiteSpace($AvatarUrl)) {
    $command += @("--avatar-url", $AvatarUrl)
}
if (-not [string]::IsNullOrWhiteSpace($AvatarModelUrl)) {
    $command += @("--avatar-model-url", $AvatarModelUrl)
}

Invoke-WithModuleStatus `
    -WorkingDirectory $repoRoot `
    -Command $command `
    -StatusDir $StatusDir `
    -ModuleName "console" `
    -ModuleLabel "Integration console" `
    -Detail "http://$HostName`:$Port" `
    -DryRun:$DryRun
