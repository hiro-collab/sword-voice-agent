param(
    [Parameter(Position = 0)]
    [ValidateSet("status", "start", "stop")]
    [string]$Command = "status",
    [string]$Profile = "thought-core-v0",
    [string]$WorkspaceRoot = "",
    [string]$StackStateDir = "",
    [string]$HomeControlConfigPath = "",
    [int]$HomeAssistantBridgePort = 8787,
    [string]$HomeAssistantBridgeHost = "127.0.0.1",
    [int]$EnvironmentStatePort = 8790,
    [int]$MediapipePort = 8765,
    [int]$MediapipeBrowserMonitorPort = 8770,
    [int]$VisionSnapshotProcessorPort = 8776,
    [int]$AituberPort = 3000,
    [string]$AituberHost = "127.0.0.1",
    [int]$TouchDesignerGuiPort = 8788,
    [string]$TouchDesignerGuiHost = "127.0.0.1",
    [string]$ThoughtCoreHost = "127.0.0.1",
    [int]$ThoughtCorePort = 18787,
    [ValidateSet(18786, 18886)]
    [int]$OpenAIBrokerPort = 18786,
    [ValidateSet("configured", "openai-compatible", "sword-openai-broker", "codex-cli", "codex-cli-luna")]
    [string]$ThoughtCoreLlmProvider = "configured",
    [string]$ThoughtCoreWatchAituberHttpTimeout = "",
    [string]$VoicevoxUrl = "",
    [int]$VoicevoxReadyTimeoutSeconds = 45,
    [ValidateSet("gui", "camera-hub", "mediamtx")]
    [string]$MediapipeMode = "mediamtx",
    [string]$MediapipeCameraName = "",
    [ValidateRange(160, 3840)]
    [int]$MediapipeCameraWidth = 1920,
    [ValidateRange(120, 2160)]
    [int]$MediapipeCameraHeight = 1080,
    [ValidateRange(1, 120)]
    [int]$MediapipeCameraFps = 30,
    [ValidateSet("auto", "mjpeg")]
    [string]$MediapipeCameraInputCodec = "mjpeg",
    [int]$MediapipeReadyTimeoutSeconds = 90,
    [ValidateSet("dshow", "testsrc")]
    [string]$MediapipeVideoSource = "dshow",
    [switch]$MediapipeOpenBrowser,
    [switch]$MediapipeNoBrowser,
    [switch]$MediapipePythonGui,
    [switch]$SkipHomeAssistantBridge,
    [switch]$SkipEnvironmentState,
    [switch]$SkipMediapipe,
    [switch]$SkipVisionSnapshotProcessor,
    [switch]$SkipAituber,
    [switch]$SkipTouchDesignerGui,
    [switch]$EnableThoughtCore,
    [switch]$SkipThoughtCore,
    [switch]$EnableThoughtCoreWatch,
    [switch]$SkipThoughtCoreWatch,
    [switch]$ThoughtCoreNoProvider,
    [switch]$StopExisting,
    [switch]$SkipVoicevoxCheck,
    [switch]$EnableHomeControlFaultInjection,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$ManifestOnly,
    [switch]$Watch,
    [int]$IntervalSeconds = 3,
    [ValidateRange(1, 65535)]
    [int]$LauncherPort = 8799,
    [string]$LauncherUrl = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
[Console]::InputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

if ($DryRun -or $ManifestOnly -or $Watch) {
    throw "launcher_compatibility_mode_not_supported"
}
if ([Environment]::GetEnvironmentVariable("SWORD_LAUNCHER_COMPAT_CLIENT_ACTIVE") -ceq "1") {
    throw "launcher_compatibility_recursion_rejected"
}

$baseUrl = if ([string]::IsNullOrWhiteSpace($LauncherUrl)) {
    "http://127.0.0.1:$LauncherPort"
}
else {
    $LauncherUrl.TrimEnd("/")
}

try {
    $baseUri = [Uri]::new($baseUrl, [UriKind]::Absolute)
}
catch {
    throw "launcher_compatibility_url_invalid"
}
$loopbackHosts = @("127.0.0.1", "localhost", "::1", "[::1]")
if (
    $baseUri.Scheme -cne "http" -or
    $loopbackHosts -notcontains $baseUri.Host.ToLowerInvariant() -or
    -not [string]::IsNullOrEmpty($baseUri.UserInfo) -or
    -not [string]::IsNullOrEmpty($baseUri.Query) -or
    -not [string]::IsNullOrEmpty($baseUri.Fragment) -or
    $baseUri.AbsolutePath -cne "/"
) {
    throw "launcher_compatibility_url_invalid"
}

$primaryProfile = $Profile -ceq "thought-core-v0"
$effectiveThoughtCoreProvider = if (
    $primaryProfile -and
    -not $PSBoundParameters.ContainsKey("ThoughtCoreLlmProvider")
) {
    "sword-openai-broker"
}
else {
    $ThoughtCoreLlmProvider
}
$effectiveEnableThoughtCore = if ($SkipThoughtCore) {
    $false
}
elseif ($PSBoundParameters.ContainsKey("EnableThoughtCore")) {
    [bool]$EnableThoughtCore
}
else {
    $primaryProfile
}
$effectiveEnableThoughtCoreWatch = if ($SkipThoughtCoreWatch) {
    $false
}
elseif ($PSBoundParameters.ContainsKey("EnableThoughtCoreWatch")) {
    [bool]$EnableThoughtCoreWatch
}
else {
    $primaryProfile
}
$effectiveMediapipeNoBrowser = if (
    $PSBoundParameters.ContainsKey("MediapipeNoBrowser") -or
    $PSBoundParameters.ContainsKey("MediapipeOpenBrowser")
) {
    [bool]$MediapipeNoBrowser
}
else {
    $primaryProfile
}
$effectiveStopExisting = if ($PSBoundParameters.ContainsKey("StopExisting")) {
    [bool]$StopExisting
}
else {
    $primaryProfile
}

$options = [ordered]@{
    HomeAssistantBridgePort = $HomeAssistantBridgePort
    HomeAssistantBridgeHost = $HomeAssistantBridgeHost
    EnvironmentStatePort = $EnvironmentStatePort
    MediapipePort = $MediapipePort
    MediapipeBrowserMonitorPort = $MediapipeBrowserMonitorPort
    VisionSnapshotProcessorPort = $VisionSnapshotProcessorPort
    AituberPort = $AituberPort
    AituberHost = $AituberHost
    TouchDesignerGuiPort = $TouchDesignerGuiPort
    TouchDesignerGuiHost = $TouchDesignerGuiHost
    ThoughtCoreHost = $ThoughtCoreHost
    ThoughtCorePort = $ThoughtCorePort
    OpenAIBrokerPort = $OpenAIBrokerPort
    ThoughtCoreLlmProvider = $effectiveThoughtCoreProvider
    VoicevoxUrl = $VoicevoxUrl
    VoicevoxReadyTimeoutSeconds = $VoicevoxReadyTimeoutSeconds
    MediapipeReadyTimeoutSeconds = $MediapipeReadyTimeoutSeconds
    MediapipeMode = $MediapipeMode
    MediapipeCameraName = $MediapipeCameraName
    MediapipeCameraWidth = $MediapipeCameraWidth
    MediapipeCameraHeight = $MediapipeCameraHeight
    MediapipeCameraFps = $MediapipeCameraFps
    MediapipeCameraInputCodec = $MediapipeCameraInputCodec
    MediapipeOpenBrowser = [bool]$MediapipeOpenBrowser
    MediapipeNoBrowser = $effectiveMediapipeNoBrowser
    MediapipePythonGui = [bool]$MediapipePythonGui
    SkipHomeAssistantBridge = [bool]$SkipHomeAssistantBridge
    SkipEnvironmentState = [bool]$SkipEnvironmentState
    SkipMediapipe = [bool]$SkipMediapipe
    SkipVisionSnapshotProcessor = [bool]$SkipVisionSnapshotProcessor
    SkipAituber = [bool]$SkipAituber
    SkipTouchDesignerGui = [bool]$SkipTouchDesignerGui
    EnableThoughtCore = $effectiveEnableThoughtCore
    EnableThoughtCoreWatch = $effectiveEnableThoughtCoreWatch
    ThoughtCoreNoProvider = [bool]$ThoughtCoreNoProvider
    StopExisting = $effectiveStopExisting
    SkipVoicevoxCheck = [bool]$SkipVoicevoxCheck
    EnableHomeControlFaultInjection = [bool]$EnableHomeControlFaultInjection
}
if (-not [string]::IsNullOrWhiteSpace($HomeControlConfigPath)) {
    $options.HomeControlConfigPath = $HomeControlConfigPath
}

$previousRecursionGuard = [Environment]::GetEnvironmentVariable(
    "SWORD_LAUNCHER_COMPAT_CLIENT_ACTIVE",
    [EnvironmentVariableTarget]::Process
)
[Environment]::SetEnvironmentVariable(
    "SWORD_LAUNCHER_COMPAT_CLIENT_ACTIVE",
    "1",
    [EnvironmentVariableTarget]::Process
)
try {
    if ($Command -ceq "status") {
        $result = Invoke-RestMethod `
            -Uri "$baseUrl/api/status" `
            -Method Get `
            -TimeoutSec 10 `
            -ErrorAction Stop
    }
    else {
        $body = if ($Command -ceq "start") {
            [ordered]@{ profileId = $Profile; options = $options }
        }
        else {
            [ordered]@{ profileId = $Profile }
        }
        $timeoutSeconds = if ($Command -ceq "start") { 600 } else { 180 }
        $result = Invoke-RestMethod `
            -Uri "$baseUrl/api/$Command" `
            -Method Post `
            -ContentType "application/json; charset=utf-8" `
            -Body ($body | ConvertTo-Json -Depth 8 -Compress) `
            -TimeoutSec $timeoutSeconds `
            -ErrorAction Stop
    }
}
catch {
    throw "launcher_api_unavailable"
}
finally {
    [Environment]::SetEnvironmentVariable(
        "SWORD_LAUNCHER_COMPAT_CLIENT_ACTIVE",
        $previousRecursionGuard,
        [EnvironmentVariableTarget]::Process
    )
}

$result | ConvertTo-Json -Depth 32
