param(
    [string]$EnvPath = ".env",
    [string]$Source = "web",
    [string]$Field = "command",
    [ValidateSet("blocking", "streaming")]
    [string]$ResponseMode = "streaming",
    [string]$StatusDir = ".cache\sword_voice_agent",
    [string]$TtsChunkUrl = "",
    [string]$TtsHttpTimeout = "",
    [string]$AituberMessageUrl = "",
    [string]$AituberHttpTimeout = "",
    [int]$AituberSpeechMaxChars = 80,
    [switch]$NoSkipExisting,
    [switch]$NoSkipShortAscii,
    [int]$ShortAsciiMaxChars = 16,
    [switch]$PrintJson,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
Import-SwordEnv -EnvPath $EnvPath
Set-SwordPythonPath
Assert-EnvPath -Name "AI_TALK_CORE_ROOT" | Out-Null
Assert-EnvValue -Name "DIFY_BASE_URL" | Out-Null
Assert-EnvValue -Name "DIFY_API_KEY" | Out-Null

if ([string]::IsNullOrWhiteSpace($TtsChunkUrl)) {
    $TtsChunkUrl = [Environment]::GetEnvironmentVariable("TTS_HTTP_CHUNK_URL", "Process")
}
if ([string]::IsNullOrWhiteSpace($TtsHttpTimeout)) {
    $TtsHttpTimeout = [Environment]::GetEnvironmentVariable("TTS_HTTP_TIMEOUT_S", "Process")
}
if ([string]::IsNullOrWhiteSpace($AituberMessageUrl)) {
    $AituberMessageUrl = [Environment]::GetEnvironmentVariable("AITUBER_MESSAGE_URL", "Process")
}
if ([string]::IsNullOrWhiteSpace($AituberHttpTimeout)) {
    $AituberHttpTimeout = [Environment]::GetEnvironmentVariable("AITUBER_HTTP_TIMEOUT_S", "Process")
}
$envAituberSpeechMaxChars = [Environment]::GetEnvironmentVariable("AITUBER_SPEECH_MAX_CHARS", "Process")
if ($AituberSpeechMaxChars -eq 80 -and -not [string]::IsNullOrWhiteSpace($envAituberSpeechMaxChars)) {
    $parsedAituberSpeechMaxChars = 0
    if ([int]::TryParse($envAituberSpeechMaxChars, [ref]$parsedAituberSpeechMaxChars)) {
        $AituberSpeechMaxChars = $parsedAituberSpeechMaxChars
    }
}

$command = @(
    "python",
    "-m",
    "sword_voice_agent.apps.watch_handoff_to_dify",
    "--source",
    $Source,
    "--field",
    $Field,
    "--response-mode",
    $ResponseMode,
    "--status-dir",
    (Resolve-SwordPath -Path $StatusDir)
)

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
if (-not $NoSkipExisting) {
    $command += "--skip-existing"
}
if (-not $NoSkipShortAscii) {
    $command += @("--skip-short-ascii", "--short-ascii-max-chars", $ShortAsciiMaxChars)
}
if ($PrintJson) {
    $command += "--print-json"
}

Invoke-WithModuleStatus `
    -WorkingDirectory $repoRoot `
    -Command $command `
    -StatusDir $StatusDir `
    -ModuleName "dify_watcher" `
    -ModuleLabel "Dify watcher" `
    -Detail "source=$Source field=$Field response_mode=$ResponseMode tts_chunk=$(-not [string]::IsNullOrWhiteSpace($TtsChunkUrl)) aituber_stream=$(-not [string]::IsNullOrWhiteSpace($AituberMessageUrl))" `
    -DryRun:$DryRun
