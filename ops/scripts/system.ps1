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
    [ValidateRange(1, 64)]
    [int]$OpenAIBrokerRequestBudget = 64,
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
    OpenAIBrokerRequestBudget = $OpenAIBrokerRequestBudget
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
    if ($Command -ceq "start") {
        $saveBody = [ordered]@{ profileId = $Profile; options = $options }
        try {
            $savedConfig = Invoke-RestMethod `
                -Uri "$baseUrl/api/save-config" `
                -Method Post `
                -ContentType "application/json; charset=utf-8" `
                -Body ($saveBody | ConvertTo-Json -Depth 8 -Compress) `
                -TimeoutSec 600 `
                -ErrorAction Stop
        }
        catch {
            throw "launcher_api_unavailable"
        }

        $savedProperties = if ($savedConfig -is [pscustomobject]) {
            @($savedConfig.PSObject.Properties)
        }
        else {
            @()
        }
        $okProperty = @($savedProperties | Where-Object { $_.Name -ceq "ok" })
        $profileProperty = @($savedProperties | Where-Object { $_.Name -ceq "profileId" })
        $identityProperty = @($savedProperties | Where-Object { $_.Name -ceq "configIdentity" })
        $configIdentity = if ($identityProperty.Count -eq 1) {
            $identityProperty[0].Value
        }
        else {
            $null
        }
        $identityProperties = if ($configIdentity -is [pscustomobject]) {
            @($configIdentity.PSObject.Properties)
        }
        else {
            @()
        }
        $hashProperty = @(
            $identityProperties |
                Where-Object { $_.Name -ceq "effective_config_sha256" }
        )
        $expectedConfigSha256 = if ($hashProperty.Count -eq 1) {
            $hashProperty[0].Value
        }
        else {
            $null
        }
        $savedIdentityIsValid = (
            $okProperty.Count -eq 1 -and
            $okProperty[0].Value -is [bool] -and
            $okProperty[0].Value -eq $true -and
            $profileProperty.Count -eq 1 -and
            $profileProperty[0].Value -is [string] -and
            $profileProperty[0].Value -ceq $Profile -and
            $configIdentity -is [pscustomobject] -and
            $hashProperty.Count -eq 1 -and
            $expectedConfigSha256 -is [string] -and
            $expectedConfigSha256.Length -eq 64 -and
            $expectedConfigSha256 -cmatch '^[a-f0-9]{64}$'
        )
        if (-not $savedIdentityIsValid) {
            throw "saved_config_identity_invalid"
        }

        $startBody = [ordered]@{
            profileId = $Profile
            expectedConfigSha256 = $expectedConfigSha256
        }
        try {
            $result = Invoke-RestMethod `
                -Uri "$baseUrl/api/start" `
                -Method Post `
                -ContentType "application/json; charset=utf-8" `
                -Body ($startBody | ConvertTo-Json -Depth 8 -Compress) `
                -TimeoutSec 600 `
                -ErrorAction Stop
        }
        catch {
            throw "launcher_api_unavailable"
        }
    }
    else {
        try {
            if ($Command -ceq "status") {
                $result = Invoke-RestMethod `
                    -Uri "$baseUrl/api/status" `
                    -Method Get `
                    -TimeoutSec 10 `
                    -ErrorAction Stop
            }
            else {
                $body = [ordered]@{ profileId = $Profile }
                $result = Invoke-RestMethod `
                    -Uri "$baseUrl/api/stop" `
                    -Method Post `
                    -ContentType "application/json; charset=utf-8" `
                    -Body ($body | ConvertTo-Json -Depth 8 -Compress) `
                    -TimeoutSec 180 `
                    -ErrorAction Stop
            }
        }
        catch {
            throw "launcher_api_unavailable"
        }
    }
}
finally {
    [Environment]::SetEnvironmentVariable(
        "SWORD_LAUNCHER_COMPAT_CLIENT_ACTIVE",
        $previousRecursionGuard,
        [EnvironmentVariableTarget]::Process
    )
}

$result | ConvertTo-Json -Depth 32
