param(
    [string]$EnvPath = ".env",
    [switch]$Preview,
    [switch]$SuppressProtobufWarnings,
    [switch]$SkipAiTalkCore,
    [switch]$SkipMediapipe,
    [switch]$SkipDifyWatch,
    [switch]$SkipConsole,
    [switch]$EnableTts,
    [switch]$DisableTts,
    [switch]$EnableAvatar,
    [switch]$DisableAvatar,
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
    [int]$StartupDelayMilliseconds = 250,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
[Console]::InputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

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
$difyBaseUrl = ""
if (-not $SkipDifyWatch) {
    $difyBaseUrl = Assert-EnvValue -Name "DIFY_BASE_URL"
    Assert-EnvValue -Name "DIFY_API_KEY" | Out-Null
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
    if (-not $SkipConsole) {
        $tcpPorts += 8790
    }
    Assert-SwordPortsAvailable `
        -TcpPorts @($tcpPorts | Select-Object -Unique) `
        -UdpPorts @(8765)
}

if ($DryRun) {
    Write-Host "[dry-run] no supervisor child processes will be started"
    Write-Host "Run again without -DryRun to start the stack in this terminal."
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

$shell = (Get-Process -Id $PID).Path

function New-SupervisorScriptCommand {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptName,
        [string[]]$ExtraArgs = @()
    )

    return @(
        $shell,
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        (Join-Path $PSScriptRoot $ScriptName),
        "-EnvPath",
        $resolvedEnvPath
    ) + $ExtraArgs
}

function Get-TtsSupervisorArgs {
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

$moduleCommands = @()
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
    $moduleCommands += [pscustomobject]@{
        Name = "ai_talk_core"
        Command = New-SupervisorScriptCommand -ScriptName "start-ai-talk-core.ps1" -ExtraArgs $aiTalkCoreArgs
    }
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
$moduleCommands += [pscustomobject]@{
    Name = "gesture_udp_receiver"
    Command = New-SupervisorScriptCommand -ScriptName "start-gesture-udp.ps1" -ExtraArgs $gestureArgs
}

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
    $moduleCommands += [pscustomobject]@{
        Name = "mediapipe_udp_publisher"
        Command = New-SupervisorScriptCommand -ScriptName "start-mediapipe-udp.ps1" -ExtraArgs $mediaArgs
    }
}

if ($ttsEnabled -and $TtsSource -eq "http") {
    $moduleCommands += [pscustomobject]@{
        Name = "tts_service"
        Command = New-SupervisorScriptCommand -ScriptName "start-tts-service.ps1" -ExtraArgs (Get-TtsSupervisorArgs)
    }
}

if (-not $SkipDifyWatch) {
    $difyWatchArgs = @("-ResponseMode", $DifyResponseMode)
    if ($ttsEnabled -and $TtsSource -eq "http") {
        $difyWatchArgs += @(
            "-TtsChunkUrl",
            "http://${TtsHttpHost}:$TtsHttpPort/api/tts/chunk",
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
    $moduleCommands += [pscustomobject]@{
        Name = "dify_watch"
        Command = New-SupervisorScriptCommand -ScriptName "start-dify-watch.ps1" -ExtraArgs $difyWatchArgs
    }
}

if ($ttsEnabled -and $TtsSource -ne "http") {
    $moduleCommands += [pscustomobject]@{
        Name = "tts_service"
        Command = New-SupervisorScriptCommand -ScriptName "start-tts-service.ps1" -ExtraArgs (Get-TtsSupervisorArgs)
    }
}

if ($avatarEnabled) {
    $moduleCommands += [pscustomobject]@{
        Name = "avatar_service"
        Command = New-SupervisorScriptCommand `
            -ScriptName "start-avatar-service.ps1" `
            -ExtraArgs @("-HostName", $AvatarHost, "-Port", $AvatarPort)
    }
}

if (-not $SkipConsole) {
    $consoleArgs = @()
    if ($avatarEnabled) {
        $consoleArgs += @("-AvatarUrl", "http://${AvatarHost}:$AvatarPort")
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
    $moduleCommands += [pscustomobject]@{
        Name = "console"
        Command = New-SupervisorScriptCommand -ScriptName "start-console.ps1" -ExtraArgs $consoleArgs
    }
}

if ($DryRun) {
    foreach ($moduleCommand in $moduleCommands) {
        Write-Host "[$($moduleCommand.Name)]"
        Write-Host (Format-CommandLine -Command $moduleCommand.Command)
    }
    return
}

function Start-SupervisedProcess {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string[]]$Command
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Command[0]
    if ($Command.Count -gt 1) {
        foreach ($argument in $Command[1..($Command.Count - 1)]) {
            $startInfo.ArgumentList.Add($argument)
        }
    }
    $startInfo.WorkingDirectory = $repoRoot
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = $utf8NoBom
    $startInfo.StandardErrorEncoding = $utf8NoBom
    $startInfo.CreateNoWindow = $true
    $startInfo.Environment["PYTHONUTF8"] = "1"
    $startInfo.Environment["PYTHONIOENCODING"] = "utf-8"
    $startInfo.Environment["NO_COLOR"] = "1"
    $startInfo.Environment["FORCE_COLOR"] = "0"
    $startInfo.Environment["TERM"] = "dumb"

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $process.EnableRaisingEvents = $true

    if (-not $process.Start()) {
        throw "failed to start $Name"
    }

    $stdoutEvent = Register-ObjectEvent `
        -InputObject $process `
        -EventName OutputDataReceived `
        -MessageData @{ Prefix = $Name; Stream = "stdout" } `
        -Action {
            $line = $EventArgs.Data
            if ($null -ne $line) {
                $cleanLine = [regex]::Replace($line, "`e\[[0-?]*[ -/]*[@-~]", "")
                [Console]::Out.WriteLine("[{0}] {1}", $Event.MessageData.Prefix, $cleanLine)
            }
        }
    $stderrEvent = Register-ObjectEvent `
        -InputObject $process `
        -EventName ErrorDataReceived `
        -MessageData @{ Prefix = $Name; Stream = "stderr" } `
        -Action {
            $line = $EventArgs.Data
            if ($null -ne $line) {
                $cleanLine = [regex]::Replace($line, "`e\[[0-?]*[ -/]*[@-~]", "")
                [Console]::Error.WriteLine("[{0}] {1}", $Event.MessageData.Prefix, $cleanLine)
            }
        }

    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()
    Write-Host "[$Name] started PID $($process.Id)"

    return [pscustomobject]@{
        Name = $Name
        Process = $process
        StdoutEvent = $stdoutEvent
        StderrEvent = $stderrEvent
        NotifiedExit = $false
    }
}

function Stop-SupervisedEvents {
    param(
        [object[]]$Children
    )

    foreach ($child in $Children) {
        foreach ($subscription in @($child.StdoutEvent, $child.StderrEvent)) {
            if ($null -ne $subscription) {
                Unregister-Event -SubscriptionId $subscription.Id -ErrorAction SilentlyContinue
                Remove-Job -Id $subscription.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Invoke-SupervisorStackStop {
    Write-Host "Stopping stack..."
    $stopArgs = @(
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        (Join-Path $PSScriptRoot "stop-full-stack.ps1"),
        "-EnvPath",
        $resolvedEnvPath,
        "-Force"
    )
    & $shell @stopArgs
}

$children = @()
$script:shutdownStarted = $false
$script:exitCode = 0

try {
    foreach ($moduleCommand in $moduleCommands) {
        $children += Start-SupervisedProcess `
            -Name $moduleCommand.Name `
            -Command $moduleCommand.Command
        if ($StartupDelayMilliseconds -gt 0) {
            Start-Sleep -Milliseconds $StartupDelayMilliseconds
        }
    }

    Write-Host "Open ai_talk_core Web UI: http://127.0.0.1:8000"
    if ($avatarEnabled) {
        Write-Host "Open Avatar service: http://${AvatarHost}:$AvatarPort"
    }
    Write-Host "Open Sword Voice Agent console: http://127.0.0.1:8790"
    Write-Host "Press Ctrl+C to stop the full stack."

    while ($true) {
        $running = 0
        foreach ($child in $children) {
            if ($child.Process.HasExited) {
                if (-not $child.NotifiedExit) {
                    $child.NotifiedExit = $true
                    $code = $child.Process.ExitCode
                    Write-Host "[$($child.Name)] exited code=$code"
                    if ($code -ne 0 -and -not $script:shutdownStarted) {
                        $script:exitCode = $code
                        $script:shutdownStarted = $true
                        Invoke-SupervisorStackStop
                    }
                }
            }
            else {
                $running += 1
            }
        }

        if ($running -eq 0) {
            break
        }
        Start-Sleep -Milliseconds 500
    }
}
catch [System.Management.Automation.PipelineStoppedException] {
    Write-Host "Ctrl+C received; requesting cooperative stack shutdown..."
}
finally {
    if (-not $script:shutdownStarted) {
        $liveChildren = @($children | Where-Object { -not $_.Process.HasExited })
        if ($liveChildren.Count -gt 0) {
            $script:shutdownStarted = $true
            Invoke-SupervisorStackStop
        }
    }
    Stop-SupervisedEvents -Children $children
}

exit $script:exitCode
