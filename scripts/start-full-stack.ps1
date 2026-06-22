param(
    [string]$EnvPath = ".env",
    [switch]$Preview,
    [switch]$SuppressProtobufWarnings,
    [switch]$SkipAiTalkCore,
    [switch]$SkipMediapipe,
    [switch]$SkipThoughtCore,
    [switch]$SkipThoughtCoreWatch,
    [switch]$SkipConsole,
    [switch]$EnableTts,
    [switch]$DisableTts,
    [switch]$EnableAvatar,
    [switch]$DisableAvatar,
    [switch]$NoAiTalkCoreIntegrationDefaults,
    [switch]$NoRecordGateAuto,
    [switch]$NoSaveHandoff,
    [string]$ThoughtCoreHost = "127.0.0.1",
    [int]$ThoughtCorePort = 18787,
    [string]$ThoughtCoreBaseUrl = "",
    [string]$GestureMinConfidence = "",
    [string]$GestureActivationDelay = "",
    [string]$GestureReleaseDelay = "",
    [string]$MediapipeLatencyProfile = "",
    [string]$MediapipeStateEvery = "",
    [string]$MediapipeControlHttpPort = "",
    [switch]$MediapipeEdgeOnly,
    [ValidateSet("status-file", "http")]
    [string]$TtsSource = "http",
    [string]$TtsEngine = "",
    [string]$TtsPlayer = "",
    [string]$TtsVoiceName = "",
    [string]$TtsPollInterval = "",
    [string]$TtsVolume = "",
    [string]$TtsAppVolume = "",
    [string]$TtsAppVolumeFile = "",
    [string]$TtsVolumeUrl = "",
    [string]$TtsVolumePreviewUrl = "",
    [string]$TtsHttpHost = "127.0.0.1",
    [string]$TtsHttpPort = "8765",
    [string]$TtsHttpChunkMaxChars = "80",
    [string]$TtsHttpTimeout = "0.75",
    [string]$AvatarHost = "127.0.0.1",
    [string]$AvatarPort = "5173",
    [string]$AvatarModelUrl = "",
    [switch]$Background,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
$resolvedEnvPath = Resolve-SwordPath -Path $EnvPath
Import-SwordEnv -EnvPath $resolvedEnvPath
Set-SwordAiTalkCoreWebTokenDefault -Generate | Out-Null
if ($EnableTts -and $DisableTts) {
    throw "Use either -EnableTts or -DisableTts, not both."
}
if ($EnableAvatar -and $DisableAvatar) {
    throw "Use either -EnableAvatar or -DisableAvatar, not both."
}
$ttsEnabled = -not $DisableTts
$avatarEnabled = -not $DisableAvatar
Assert-EnvPath -Name "AI_TALK_CORE_ROOT" | Out-Null
if (-not $SkipMediapipe) {
    Assert-EnvPath -Name "MEDIAPIPE_SWORD_SIGN_ROOT" | Out-Null
}
if ($ttsEnabled) {
    Assert-EnvPath -Name "TTS_SERVICE_ROOT" | Out-Null
}
if ($avatarEnabled) {
    Assert-EnvPath -Name "AVATAR_SERVICE_ROOT" | Out-Null
}

$shell = (Get-Process -Id $PID).Path

if ([string]::IsNullOrWhiteSpace($ThoughtCoreBaseUrl)) {
    $ThoughtCoreBaseUrl = [Environment]::GetEnvironmentVariable("THOUGHT_CORE_BASE_URL", "Process")
}
if ([string]::IsNullOrWhiteSpace($ThoughtCoreBaseUrl)) {
    $thoughtCoreClientHost = if ($ThoughtCoreHost -eq "0.0.0.0") { "127.0.0.1" } else { $ThoughtCoreHost }
    $ThoughtCoreBaseUrl = "http://${thoughtCoreClientHost}:$ThoughtCorePort"
}

if ([string]::IsNullOrWhiteSpace($TtsVolumeUrl)) {
    $TtsVolumeUrl = [Environment]::GetEnvironmentVariable("TTS_VOLUME_URL", "Process")
}
if ([string]::IsNullOrWhiteSpace($TtsVolumeUrl) -and $TtsSource -eq "http") {
    $TtsVolumeUrl = "http://${TtsHttpHost}:$TtsHttpPort/api/volume"
}
if ([string]::IsNullOrWhiteSpace($TtsVolumePreviewUrl)) {
    $TtsVolumePreviewUrl = [Environment]::GetEnvironmentVariable(
        "TTS_VOLUME_PREVIEW_URL",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($TtsVolumePreviewUrl) -and $ttsEnabled -and $TtsSource -eq "http") {
    $TtsVolumePreviewUrl = "http://${TtsHttpHost}:$TtsHttpPort/api/volume/preview"
}
if ([string]::IsNullOrWhiteSpace($AvatarModelUrl)) {
    $AvatarModelUrl = [Environment]::GetEnvironmentVariable("AVATAR_MODEL_URL", "Process")
}
if ($avatarEnabled) {
    $AvatarModelUrl = Resolve-SwordAvatarModelUrl -ModelUrl $AvatarModelUrl
}
if ([string]::IsNullOrWhiteSpace($MediapipeControlHttpPort)) {
    $MediapipeControlHttpPort = [Environment]::GetEnvironmentVariable(
        "MEDIAPIPE_SWORD_SIGN_CONTROL_HTTP_PORT",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($MediapipeControlHttpPort)) {
    $MediapipeControlHttpPort = "18765"
}
if (-not $DryRun) {
    $tcpPorts = @()
    if (-not $SkipAiTalkCore) {
        $tcpPorts += 8000
    }
    if ($ttsEnabled -and $TtsSource -eq "http") {
        $tcpPorts += [int]$TtsHttpPort
    }
    if ($avatarEnabled) {
        $tcpPorts += [int]$AvatarPort
    }
    if (-not $SkipMediapipe) {
        $tcpPorts += [int]$MediapipeControlHttpPort
    }
    if (-not $SkipThoughtCore) {
        $tcpPorts += [int]$ThoughtCorePort
    }
    if (-not $SkipConsole) {
        $tcpPorts += 8790
    }
    $udpPorts = @(8765)
    $tcpCheckPorts = @($tcpPorts | Select-Object -Unique)
    $udpCheckPorts = @($udpPorts | Select-Object -Unique)
    Assert-SwordPortsAvailable `
        -TcpPorts $tcpCheckPorts `
        -UdpPorts $udpCheckPorts
}

if ($DryRun) {
    Write-Host "[dry-run] no PowerShell windows will be opened"
    Write-Host "Run again without -DryRun to start the stack."
}

function Start-SwordWindow {
    param(
        [Parameter(Mandatory = $true)][string]$Title,
        [Parameter(Mandatory = $true)][string]$ScriptName,
        [string[]]$ExtraArgs = @()
    )

    $scriptPath = Join-Path $PSScriptRoot $ScriptName
    $arguments = @(
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $scriptPath,
        "-EnvPath",
        $resolvedEnvPath
    ) + $ExtraArgs
    if (-not $Background) {
        $arguments = @("-NoExit") + $arguments
    }

    if ($DryRun) {
        Write-Host "[$Title]"
        $previewCommand = @($shell) + $arguments
        Write-Host (Format-CommandLine -Command $previewCommand)
        return
    }

    if ($Background) {
        $logDir = Resolve-SwordPath -Path ".cache\sword_voice_agent\logs"
        New-Item -ItemType Directory -Force -Path $logDir | Out-Null
        $safeTitle = $Title -replace "[^A-Za-z0-9_-]", "_"
        $stdoutPath = Join-Path $logDir "$safeTitle.out.log"
        $stderrPath = Join-Path $logDir "$safeTitle.err.log"
        Start-Process `
            -FilePath $shell `
            -ArgumentList $arguments `
            -WorkingDirectory $repoRoot `
            -WindowStyle Hidden `
            -RedirectStandardOutput $stdoutPath `
            -RedirectStandardError $stderrPath
        Write-Host "[$Title] background logs: $stdoutPath / $stderrPath"
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
    if (-not [string]::IsNullOrWhiteSpace($TtsVolume)) {
        $ttsArgs += @("-Volume", $TtsVolume)
    }
    if (-not [string]::IsNullOrWhiteSpace($TtsAppVolume)) {
        $ttsArgs += @("-AppVolume", $TtsAppVolume)
    }
    if (-not [string]::IsNullOrWhiteSpace($TtsAppVolumeFile)) {
        $ttsArgs += @("-AppVolumeFile", $TtsAppVolumeFile)
    }
    return $ttsArgs
}

function Start-TtsServiceWindow {
    Start-SwordWindow `
        -Title "tts_service" `
        -ScriptName "start-tts-service.ps1" `
        -ExtraArgs (Get-TtsWindowArgs)
}

function Start-AvatarServiceWindow {
    Start-SwordWindow `
        -Title "avatar_service" `
        -ScriptName "start-avatar-service.ps1" `
        -ExtraArgs @(
            "-HostName",
            $AvatarHost,
            "-Port",
            $AvatarPort
        )
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
    if (-not [string]::IsNullOrWhiteSpace($MediapipeLatencyProfile)) {
        $mediaArgs += @("-LatencyProfile", $MediapipeLatencyProfile)
    }
    if (-not [string]::IsNullOrWhiteSpace($MediapipeStateEvery)) {
        $mediaArgs += @("-StateEvery", $MediapipeStateEvery)
    }
    if ($MediapipeEdgeOnly) {
        $mediaArgs += "-EdgeOnly"
    }
    if (-not [string]::IsNullOrWhiteSpace($MediapipeControlHttpPort)) {
        $mediaArgs += @("-ControlHttpPort", $MediapipeControlHttpPort)
    }
    Start-SwordWindow `
        -Title "mediapipe_udp_publisher" `
        -ScriptName "start-mediapipe-udp.ps1" `
        -ExtraArgs $mediaArgs
}

if ($ttsEnabled -and $TtsSource -eq "http") {
    Start-TtsServiceWindow
}

if (-not $SkipThoughtCore) {
    Start-SwordWindow `
        -Title "thought_core_api" `
        -ScriptName "start-thought-core.ps1" `
        -ExtraArgs @(
            "-HostName",
            $ThoughtCoreHost,
            "-Port",
            [string]$ThoughtCorePort
        )
}

if (-not $SkipThoughtCoreWatch) {
    $thoughtCoreWatchArgs = @("-ThoughtCoreBaseUrl", $ThoughtCoreBaseUrl)
    if ($ttsEnabled -and $TtsSource -eq "http") {
        $ttsChunkUrl = "http://${TtsHttpHost}:$TtsHttpPort/api/tts/chunk"
        $thoughtCoreWatchArgs += @(
            "-TtsChunkUrl",
            $ttsChunkUrl,
            "-TtsHttpTimeout",
            $TtsHttpTimeout
        )
    }
    Start-SwordWindow `
        -Title "thought_core_watcher" `
        -ScriptName "start-thought-core-watch.ps1" `
        -ExtraArgs $thoughtCoreWatchArgs
}

if ($ttsEnabled -and $TtsSource -ne "http") {
    Start-TtsServiceWindow
}

if ($avatarEnabled) {
    Start-AvatarServiceWindow
}

if (-not $SkipConsole) {
    $consoleArgs = @()
    if ($avatarEnabled) {
        $consoleArgs += @(
            "-AvatarUrl",
            "http://${AvatarHost}:$AvatarPort"
        )
    }
    if (-not [string]::IsNullOrWhiteSpace($AvatarModelUrl)) {
        $consoleArgs += @("-AvatarModelUrl", $AvatarModelUrl)
    }
    if (-not [string]::IsNullOrWhiteSpace($TtsAppVolumeFile)) {
        $consoleArgs += @("-TtsAppVolumeFile", $TtsAppVolumeFile)
    }
    if (-not [string]::IsNullOrWhiteSpace($TtsVolumeUrl)) {
        $consoleArgs += @("-TtsVolumeUrl", $TtsVolumeUrl)
    }
    if ($ttsEnabled -and -not [string]::IsNullOrWhiteSpace($TtsVolumePreviewUrl)) {
        $consoleArgs += @("-TtsVolumePreviewUrl", $TtsVolumePreviewUrl)
    }
    if (-not [string]::IsNullOrWhiteSpace($ThoughtCoreBaseUrl)) {
        $consoleArgs += @("-ThoughtCoreBaseUrl", $ThoughtCoreBaseUrl)
    }
    Start-SwordWindow `
        -Title "console" `
        -ScriptName "start-console.ps1" `
        -ExtraArgs $consoleArgs
}

Write-Host "Open ai_talk_core Web UI: http://127.0.0.1:8000"
if ($avatarEnabled) {
    Write-Host "Open Avatar service: http://${AvatarHost}:$AvatarPort"
}
Write-Host "Open Sword Voice Agent console: http://127.0.0.1:8790"
if ($Background) {
    Write-Host "Stop background stack: .\scripts\stop-full-stack.ps1 -Force"
}
