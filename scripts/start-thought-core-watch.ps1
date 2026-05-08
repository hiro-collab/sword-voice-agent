param(
    [string]$EnvPath = ".env",
    [string]$AiTalkCoreRoot = "",
    [string]$Source = "web",
    [string]$Field = "command",
    [string]$ThoughtCoreBaseUrl = "",
    [string]$SessionId = "",
    [string]$Locale = "",
    [string]$StatusDir = ".cache\sword_voice_agent",
    [string]$TtsChunkUrl = "",
    [string]$TtsHttpTimeout = "",
    [string]$AituberMessageUrl = "",
    [string]$AituberHttpTimeout = "",
    [int]$AituberSpeechMaxChars = 80,
    [int]$AituberPort = 0,
    [ValidateSet("", "auto", "off")]
    [string]$LocalAckMode = "",
    [switch]$NoSkipExisting,
    [switch]$SendNoSpeech,
    [switch]$PrintEvents,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
$workspaceRoot = Get-SwordWorkspaceRoot
$resolvedEnvPath = Resolve-SwordPath -Path $EnvPath
if (Test-Path -LiteralPath $resolvedEnvPath -PathType Leaf) {
    Import-SwordEnv -EnvPath $resolvedEnvPath
}
else {
    Write-Warning "env file not found, continuing with process environment: $resolvedEnvPath"
}
Set-SwordPythonPath

if ([string]::IsNullOrWhiteSpace($AiTalkCoreRoot)) {
    $AiTalkCoreRoot = [Environment]::GetEnvironmentVariable("AI_TALK_CORE_ROOT", "Process")
}
if ([string]::IsNullOrWhiteSpace($AiTalkCoreRoot)) {
    $AiTalkCoreRoot = Join-Path $workspaceRoot "organs\voice\ai-talk-core"
}
$AiTalkCoreRoot = Resolve-SwordPath -Path $AiTalkCoreRoot -BasePath $repoRoot
if (-not (Test-Path -LiteralPath $AiTalkCoreRoot -PathType Container)) {
    throw "ai-talk-core root not found: $AiTalkCoreRoot"
}

if ([string]::IsNullOrWhiteSpace($ThoughtCoreBaseUrl)) {
    $ThoughtCoreBaseUrl = [Environment]::GetEnvironmentVariable("THOUGHT_CORE_BASE_URL", "Process")
}
if ([string]::IsNullOrWhiteSpace($ThoughtCoreBaseUrl)) {
    $ThoughtCoreBaseUrl = "http://127.0.0.1:18787"
}
$env:THOUGHT_CORE_BASE_URL = $ThoughtCoreBaseUrl

if ([string]::IsNullOrWhiteSpace($Locale)) {
    $Locale = [Environment]::GetEnvironmentVariable("THOUGHT_CORE_LOCALE", "Process")
}
if ([string]::IsNullOrWhiteSpace($Locale)) {
    $Locale = "ja-JP"
}
if ([string]::IsNullOrWhiteSpace($TtsChunkUrl)) {
    $TtsChunkUrl = [Environment]::GetEnvironmentVariable("TTS_HTTP_CHUNK_URL", "Process")
}
if ([string]::IsNullOrWhiteSpace($TtsHttpTimeout)) {
    $TtsHttpTimeout = [Environment]::GetEnvironmentVariable("TTS_HTTP_TIMEOUT_S", "Process")
}
if ([string]::IsNullOrWhiteSpace($AituberMessageUrl)) {
    $AituberMessageUrl = [Environment]::GetEnvironmentVariable("AITUBER_MESSAGE_URL", "Process")
}
if ([string]::IsNullOrWhiteSpace($AituberMessageUrl) -and $AituberPort -gt 0) {
    $AituberMessageUrl = "http://127.0.0.1:$AituberPort/api/messages?clientId=thought-core&type=direct_send"
}
if ([string]::IsNullOrWhiteSpace($AituberHttpTimeout)) {
    $AituberHttpTimeout = [Environment]::GetEnvironmentVariable("AITUBER_HTTP_TIMEOUT_S", "Process")
}
if ([string]::IsNullOrWhiteSpace($LocalAckMode)) {
    $LocalAckMode = [Environment]::GetEnvironmentVariable("THOUGHT_CORE_LOCAL_ACK_MODE", "Process")
}
if ([string]::IsNullOrWhiteSpace($LocalAckMode)) {
    $LocalAckMode = "auto"
}

$command = @(
    "uv",
    "run",
    "python",
    "-m",
    "sword_voice_agent.apps.watch_handoff_to_thought_core",
    "--ai-talk-core-root",
    $AiTalkCoreRoot,
    "--source",
    $Source,
    "--field",
    $Field,
    "--locale",
    $Locale,
    "--status-dir",
    (Resolve-SwordPath -Path $StatusDir)
)

if (-not [string]::IsNullOrWhiteSpace($SessionId)) {
    $command += @("--session-id", $SessionId)
}
if (-not [string]::IsNullOrWhiteSpace($TtsChunkUrl)) {
    $command += @("--tts-chunk-url", $TtsChunkUrl)
}
if (-not [string]::IsNullOrWhiteSpace($TtsHttpTimeout)) {
    $command += @("--tts-http-timeout-s", $TtsHttpTimeout)
}
if (-not [string]::IsNullOrWhiteSpace($AituberMessageUrl)) {
    $command += @("--aituber-message-url", $AituberMessageUrl)
}
if (-not [string]::IsNullOrWhiteSpace($AituberHttpTimeout)) {
    $command += @("--aituber-http-timeout-s", $AituberHttpTimeout)
}
if ($AituberSpeechMaxChars -gt 0) {
    $command += @("--aituber-speech-max-chars", $AituberSpeechMaxChars)
}
if (-not [string]::IsNullOrWhiteSpace($LocalAckMode)) {
    $command += @("--local-ack-mode", $LocalAckMode)
}
if (-not $NoSkipExisting) {
    $command += "--skip-existing"
}
if ($SendNoSpeech) {
    $command += "--send-no-speech"
}
if ($PrintEvents) {
    $command += "--print-events"
}

Invoke-WithModuleStatus `
    -WorkingDirectory $repoRoot `
    -Command $command `
    -StatusDir $StatusDir `
    -ModuleName "thought_core_watcher" `
    -ModuleLabel "thought-core watcher" `
    -Detail "source=$Source field=$Field base_url=$ThoughtCoreBaseUrl aituber_stream=$(-not [string]::IsNullOrWhiteSpace($AituberMessageUrl)) tts_chunk=$(-not [string]::IsNullOrWhiteSpace($TtsChunkUrl))" `
    -DryRun:$DryRun
