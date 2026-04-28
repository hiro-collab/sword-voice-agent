param(
    [string]$EnvPath = ".env",
    [switch]$Preview,
    [switch]$SuppressProtobufWarnings,
    [switch]$SkipAiTalkCore,
    [switch]$SkipMediapipe,
    [switch]$SkipDifyWatch,
    [switch]$SkipConsole,
    [switch]$SkipDockerCheck,
    [switch]$NoStartDockerDesktop,
    [int]$DockerWaitSeconds = 120,
    [int]$DifyWaitSeconds = 90,
    [switch]$NoAiTalkCoreIntegrationDefaults,
    [switch]$NoRecordGateAuto,
    [switch]$NoSaveHandoff,
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

Start-SwordWindow -Title "gesture_udp_receiver" -ScriptName "start-gesture-udp.ps1"

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

if (-not $SkipDifyWatch) {
    Start-SwordWindow -Title "dify_watch" -ScriptName "start-dify-watch.ps1"
}

if (-not $SkipConsole) {
    Start-SwordWindow -Title "console" -ScriptName "start-console.ps1"
}

Write-Host "Open ai_talk_core Web UI: http://127.0.0.1:8000"
Write-Host "Open Sword Voice Agent console: http://127.0.0.1:8790"
