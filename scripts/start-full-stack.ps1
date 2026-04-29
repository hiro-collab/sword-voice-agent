param(
    [string]$EnvPath = ".env",
    [switch]$Preview,
    [switch]$SuppressProtobufWarnings,
    [switch]$SkipAiTalkCore,
    [switch]$SkipMediapipe,
    [switch]$SkipDifyWatch,
    [switch]$SkipConsole,
    [switch]$EnableTts,
    [switch]$SkipDockerCheck,
    [switch]$NoStartDockerDesktop,
    [int]$DockerWaitSeconds = 120,
    [int]$DifyWaitSeconds = 90,
    [switch]$NoAiTalkCoreIntegrationDefaults,
    [switch]$NoRecordGateAuto,
    [switch]$NoSaveHandoff,
    [switch]$NoSkipShortAscii,
    [int]$ShortAsciiMaxChars = 16,
    [ValidateSet("blocking", "streaming")]
    [string]$DifyResponseMode = "streaming",
    [string]$GestureMinConfidence = "",
    [string]$GestureActivationDelay = "",
    [string]$GestureReleaseDelay = "",
    [ValidateSet("status-file", "http")]
    [string]$TtsSource = "http",
    [string]$TtsEngine = "",
    [string]$TtsPlayer = "",
    [string]$TtsVoiceName = "",
    [string]$TtsPollInterval = "",
    [string]$TtsHttpHost = "127.0.0.1",
    [string]$TtsHttpPort = "8765",
    [string]$TtsHttpChunkMaxChars = "80",
    [string]$TtsHttpTimeout = "0.75",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
$resolvedEnvPath = Resolve-SwordPath -Path $EnvPath
Import-SwordEnv -EnvPath $resolvedEnvPath
Set-SwordAiTalkCoreWebTokenDefault -Generate | Out-Null
Assert-EnvPath -Name "AI_TALK_CORE_ROOT" | Out-Null
if (-not $SkipMediapipe) {
    Assert-EnvPath -Name "MEDIAPIPE_SWORD_SIGN_ROOT" | Out-Null
}
if ($EnableTts) {
    Assert-EnvPath -Name "TTS_SERVICE_ROOT" | Out-Null
}
$difyBaseUrl = ""
if (-not $SkipDifyWatch) {
    $difyBaseUrl = Assert-EnvValue -Name "DIFY_BASE_URL"
    Assert-EnvValue -Name "DIFY_API_KEY" | Out-Null
}

$shell = (Get-Process -Id $PID).Path

if ($DryRun) {
    Write-Host "[dry-run] no PowerShell windows will be opened"
    Write-Host "Run again without -DryRun to start the stack."
    if (-not $SkipDifyWatch -and -not $SkipDockerCheck -and (Test-SwordLoopbackUrl -Url $difyBaseUrl)) {
        Write-Host "[dry-run] Docker Desktop and Dify API will be checked before starting Dify watcher."
    }
}
elseif (-not $SkipDifyWatch -and -not $SkipDockerCheck -and (Test-SwordLoopbackUrl -Url $difyBaseUrl)) {
    if (-not (Test-SwordHttpReachable -Url $difyBaseUrl)) {
        Ensure-SwordDockerDesktop `
            -WaitSeconds $DockerWaitSeconds `
            -NoStart:$NoStartDockerDesktop
        if (-not (Wait-SwordHttpReachable `
            -Url $difyBaseUrl `
            -WaitSeconds $DifyWaitSeconds `
            -Label "Dify API")) {
            throw (
                "Dify API is not reachable at $difyBaseUrl. " +
                "Docker Desktop may be running, but Dify containers are not ready."
            )
        }
    }
    else {
        Write-Host "Dify API is reachable: $difyBaseUrl"
    }
}

function Start-SwordWindow {
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][string]$ScriptName,
        [string[]]$ExtraArgs = @()
    )

    $scriptPath = Join-Path $PSScriptRoot $ScriptName
    $arguments = @(
        "-NoExit",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $scriptPath,
        "-EnvPath",
        $resolvedEnvPath
    ) + $ExtraArgs

    if ($DryRun) {
        Write-Host "[$Title]"
        $previewCommand = @($shell) + $arguments
        Write-Host (Format-CommandLine -Command $previewCommand)
        return
    }

    Start-Process `
        -FilePath $shell `
        -ArgumentList $arguments `
        -WorkingDirectory $repoRoot `
        -WindowStyle Normal
}

function Get-TtsWindowArgs {
    $ttsArgs = @("-Source", $TtsSource)
    if ($TtsSource -eq "http") {
        $ttsArgs += @(
            "-HttpHost",
            $TtsHttpHost,
            "-HttpPort",
            $TtsHttpPort,
            "-HttpChunkMaxChars",
            $TtsHttpChunkMaxChars
        )
    }
    if (-not [string]::IsNullOrWhiteSpace($TtsEngine)) {
        $ttsArgs += @("-Engine", $TtsEngine)
    }
    if (-not [string]::IsNullOrWhiteSpace($TtsPlayer)) {
        $ttsArgs += @("-Player", $TtsPlayer)
    }
    if (-not [string]::IsNullOrWhiteSpace($TtsVoiceName)) {
        $ttsArgs += @("-VoiceName", $TtsVoiceName)
    }
    if (-not [string]::IsNullOrWhiteSpace($TtsPollInterval)) {
        $ttsArgs += @("-PollInterval", $TtsPollInterval)
    }
    return $ttsArgs
}

function Start-TtsServiceWindow {
    Start-SwordWindow `
        -Title "tts_service" `
        -ScriptName "start-tts-service.ps1" `
        -ExtraArgs (Get-TtsWindowArgs)
}

if (-not $SkipAiTalkCore) {
    $aiTalkCoreArgs = @()
    if ($NoAiTalkCoreIntegrationDefaults) {
        $aiTalkCoreArgs += "-NoIntegrationDefaults"
    }
    if ($NoRecordGateAuto) {
        $aiTalkCoreArgs += "-NoRecordGateAuto"
    }
    if ($NoSaveHandoff) {
        $aiTalkCoreArgs += "-NoSaveHandoff"
    }
    Start-SwordWindow `
        -Title "ai_talk_core" `
        -ScriptName "start-ai-talk-core.ps1" `
        -ExtraArgs $aiTalkCoreArgs
}

$gestureArgs = @()
if (-not [string]::IsNullOrWhiteSpace($GestureMinConfidence)) {
    $gestureArgs += @("-MinConfidence", $GestureMinConfidence)
}
if (-not [string]::IsNullOrWhiteSpace($GestureActivationDelay)) {
    $gestureArgs += @("-ActivationDelay", $GestureActivationDelay)
}
if (-not [string]::IsNullOrWhiteSpace($GestureReleaseDelay)) {
    $gestureArgs += @("-ReleaseDelay", $GestureReleaseDelay)
}
Start-SwordWindow `
    -Title "gesture_udp_receiver" `
    -ScriptName "start-gesture-udp.ps1" `
    -ExtraArgs $gestureArgs

if (-not $SkipMediapipe) {
    $mediaArgs = @()
    if ($Preview) {
        $mediaArgs += "-Preview"
    }
    if ($SuppressProtobufWarnings) {
        $mediaArgs += "-SuppressProtobufWarnings"
    }
    Start-SwordWindow `
        -Title "mediapipe_udp_publisher" `
        -ScriptName "start-mediapipe-udp.ps1" `
        -ExtraArgs $mediaArgs
}

if ($EnableTts -and $TtsSource -eq "http") {
    Start-TtsServiceWindow
}

if (-not $SkipDifyWatch) {
    $difyWatchArgs = @("-ResponseMode", $DifyResponseMode)
    if ($EnableTts -and $TtsSource -eq "http") {
        $ttsChunkUrl = "http://${TtsHttpHost}:$TtsHttpPort/api/tts/chunk"
        $difyWatchArgs += @(
            "-TtsChunkUrl",
            $ttsChunkUrl,
            "-TtsHttpTimeout",
            $TtsHttpTimeout
        )
    }
    if ($NoSkipShortAscii) {
        $difyWatchArgs += "-NoSkipShortAscii"
    }
    else {
        $difyWatchArgs += @("-ShortAsciiMaxChars", [string]$ShortAsciiMaxChars)
    }
    Start-SwordWindow `
        -Title "dify_watch" `
        -ScriptName "start-dify-watch.ps1" `
        -ExtraArgs $difyWatchArgs
}

if ($EnableTts -and $TtsSource -ne "http") {
    Start-TtsServiceWindow
}

if (-not $SkipConsole) {
    Start-SwordWindow -Title "console" -ScriptName "start-console.ps1"
}

Write-Host "Open ai_talk_core Web UI: http://127.0.0.1:8000"
Write-Host "Open Sword Voice Agent console: http://127.0.0.1:8790"
