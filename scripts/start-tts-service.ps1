param(
    [string]$EnvPath = ".env",
    [ValidateSet("", "status-file", "http")]
    [string]$Source = "",
    [string]$SwordStatusDir = ".cache\sword_voice_agent",
    [string]$OutputStatusDir = "",
    [string]$PollInterval = "",
    [string]$HttpHost = "",
    [string]$HttpPort = "",
    [string]$HttpChunkMaxChars = "",
    [string]$Engine = "",
    [string]$Player = "",
    [string]$VoiceName = "",
    [string]$Rate = "",
    [string]$Volume = "",
    [string]$OutputAudioDir = "",
    [switch]$Once,
    [switch]$HealthJson,
    [switch]$ListVoices,
    [switch]$Json,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
Import-SwordEnv -EnvPath $EnvPath
$ttsServiceRoot = Assert-EnvPath -Name "TTS_SERVICE_ROOT"

if ([string]::IsNullOrWhiteSpace($Source)) {
    $Source = [Environment]::GetEnvironmentVariable("TTS_SOURCE", "Process")
}
if ([string]::IsNullOrWhiteSpace($PollInterval)) {
    $PollInterval = [Environment]::GetEnvironmentVariable("TTS_POLL_INTERVAL", "Process")
}
if ([string]::IsNullOrWhiteSpace($HttpHost)) {
    $HttpHost = [Environment]::GetEnvironmentVariable("TTS_HTTP_HOST", "Process")
}
if ([string]::IsNullOrWhiteSpace($HttpPort)) {
    $HttpPort = [Environment]::GetEnvironmentVariable("TTS_HTTP_PORT", "Process")
}
if ([string]::IsNullOrWhiteSpace($HttpChunkMaxChars)) {
    $HttpChunkMaxChars = [Environment]::GetEnvironmentVariable("TTS_HTTP_CHUNK_MAX_CHARS", "Process")
}
if ([string]::IsNullOrWhiteSpace($OutputStatusDir)) {
    $OutputStatusDir = [Environment]::GetEnvironmentVariable("TTS_OUTPUT_STATUS_DIR", "Process")
}
if ([string]::IsNullOrWhiteSpace($Engine)) {
    $Engine = [Environment]::GetEnvironmentVariable("TTS_ENGINE", "Process")
}
if ([string]::IsNullOrWhiteSpace($Player)) {
    $Player = [Environment]::GetEnvironmentVariable("TTS_PLAYER", "Process")
}
if ([string]::IsNullOrWhiteSpace($VoiceName)) {
    $VoiceName = [Environment]::GetEnvironmentVariable("TTS_VOICE_NAME", "Process")
}
if ([string]::IsNullOrWhiteSpace($Rate)) {
    $Rate = [Environment]::GetEnvironmentVariable("TTS_RATE", "Process")
}
if ([string]::IsNullOrWhiteSpace($Volume)) {
    $Volume = [Environment]::GetEnvironmentVariable("TTS_VOLUME", "Process")
}
if ([string]::IsNullOrWhiteSpace($OutputAudioDir)) {
    $OutputAudioDir = [Environment]::GetEnvironmentVariable("TTS_OUTPUT_AUDIO_DIR", "Process")
}

if ([string]::IsNullOrWhiteSpace($PollInterval)) {
    $PollInterval = "1.0"
}
if ([string]::IsNullOrWhiteSpace($Source)) {
    $Source = "status-file"
}
if ([string]::IsNullOrWhiteSpace($HttpHost)) {
    $HttpHost = "127.0.0.1"
}
if ([string]::IsNullOrWhiteSpace($HttpPort)) {
    $HttpPort = "8765"
}
if ([string]::IsNullOrWhiteSpace($HttpChunkMaxChars)) {
    $HttpChunkMaxChars = "80"
}
if ([string]::IsNullOrWhiteSpace($OutputStatusDir)) {
    $OutputStatusDir = ".cache\tts_service"
}
if ([string]::IsNullOrWhiteSpace($Engine)) {
    $Engine = "windows-sapi"
}
if ([string]::IsNullOrWhiteSpace($Player)) {
    $Player = "speaker"
}
if ([string]::IsNullOrWhiteSpace($Rate)) {
    $Rate = "0"
}
if ([string]::IsNullOrWhiteSpace($Volume)) {
    $Volume = "100"
}
if ([string]::IsNullOrWhiteSpace($OutputAudioDir)) {
    $OutputAudioDir = ".cache\tts_service\audio_output"
}
if ($Engine -eq "noop") {
    $Player = "noop"
}

$resolvedSwordStatusDir = Resolve-SwordPath -Path $SwordStatusDir -BasePath $repoRoot
$resolvedOutputStatusDir = Resolve-SwordPath -Path $OutputStatusDir -BasePath $repoRoot
$resolvedOutputAudioDir = Resolve-SwordPath -Path $OutputAudioDir -BasePath $repoRoot

$command = @(
    "python",
    "-m",
    "tts_service.apps.watch_sword_response",
    "--source",
    $Source,
    "--output-status-dir",
    $resolvedOutputStatusDir,
    "--engine",
    $Engine,
    "--player",
    $Player,
    "--output-audio-dir",
    $resolvedOutputAudioDir,
    "--rate",
    $Rate,
    "--volume",
    $Volume
)

if ($Source -eq "http") {
    $command += @(
        "--http-host",
        $HttpHost,
        "--http-port",
        $HttpPort,
        "--http-chunk-max-chars",
        $HttpChunkMaxChars
    )
}
else {
    $command += @(
        "--status-dir",
        $resolvedSwordStatusDir,
        "--poll-interval",
        $PollInterval
    )
}

if (-not [string]::IsNullOrWhiteSpace($VoiceName)) {
    $command += @("--voice-name", $VoiceName)
}
if ($Once) {
    $command += "--once"
}
if ($HealthJson) {
    $command += "--health-json"
}
if ($ListVoices) {
    $command += "--list-voices"
}
if ($Json) {
    $command += "--json"
}

Invoke-WithModuleStatus `
    -WorkingDirectory $ttsServiceRoot `
    -Command $command `
    -StatusDir $SwordStatusDir `
    -ModuleName "tts_service" `
    -ModuleLabel "TTS service" `
    -Detail "source=$Source engine=$Engine player=$Player status=$(Split-Path -Leaf $resolvedOutputStatusDir)" `
    -DryRun:$DryRun
