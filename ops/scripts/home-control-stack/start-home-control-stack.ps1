param(
    [string]$WorkspaceRoot = "",
    [string]$StackStateDir = "",
    [string]$HomeAssistantServerRoot = "",
    [string]$MediapipeRoot = "",
    [string]$VisionSnapshotProcessorRoot = "",
    [string]$AituberRoot = "",
    [string]$TouchDesignerGuiRoot = "",
    [string]$ThoughtCoreRoot = "",
    [string]$EnvironmentStateServerRoot = "",
    [int]$HomeAssistantBridgePort = 8787,
    [string]$HomeAssistantBridgeHost = "127.0.0.1",
    [string]$HomeControlConfigPath = "",
    [int]$EnvironmentStatePort = 8790,
    [string]$EnvironmentStateHost = "127.0.0.1",
    [int]$MediapipePort = 8765,
    [int]$MediapipeBrowserMonitorPort = 8770,
    [int]$VisionSnapshotProcessorPort = 8776,
    [int]$AituberPort = 3000,
    [string]$AituberHost = "127.0.0.1",
    [int]$TouchDesignerGuiPort = 8788,
    [string]$TouchDesignerGuiHost = "127.0.0.1",
    [string]$TouchDesignerUdpHost = "127.0.0.1",
    [int]$TouchDesignerUdpPort = 9001,
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
    [string]$MediapipeCameraName = "Logitech StreamCam",
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
    [switch]$SkipVoicevoxCheck,
    [switch]$SkipHomeAssistantBridge,
    [switch]$SkipEnvironmentState,
    [switch]$SkipMediapipe,
    [switch]$SkipVisionSnapshotProcessor,
    [switch]$SkipAituber,
    [switch]$SkipTouchDesignerGui,
    [switch]$EnableThoughtCore,
    [switch]$EnableThoughtCoreWatch,
    [switch]$ThoughtCoreNoProvider,
    [switch]$StopExisting,
    [switch]$EnableHomeControlFaultInjection,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "..\..\..\scripts\common.ps1")

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
[Console]::InputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

$script:FixedStartFailureClass = ""

function Write-FixedStartFailureArtifact {
    param([Parameter(Mandatory = $true)][string]$FailureClass)

    $artifactPath = [Environment]::GetEnvironmentVariable("SWORD_FIXED_START_FAILURE_FILE")
    $stateDir = [Environment]::GetEnvironmentVariable("HOME_CONTROL_STACK_STATE_DIR")
    if ([string]::IsNullOrWhiteSpace($artifactPath) -or [string]::IsNullOrWhiteSpace($stateDir)) {
        return
    }

    try {
        $fullStateDir = [System.IO.Path]::GetFullPath($stateDir).TrimEnd(
            [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
        )
        $fullArtifactPath = [System.IO.Path]::GetFullPath($artifactPath)
        $artifactParent = [System.IO.Path]::GetDirectoryName($fullArtifactPath).TrimEnd(
            [char[]]@([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
        )
        $artifactName = [System.IO.Path]::GetFileName($fullArtifactPath)
        if (-not [string]::Equals($artifactParent, $fullStateDir, [System.StringComparison]::OrdinalIgnoreCase)) {
            return
        }
        if ($artifactName -notmatch '^\.launcher-fixed-start-[0-9]+-[0-9a-fA-F-]{36}\.cause$') {
            return
        }

        $payload = [System.Text.Encoding]::ASCII.GetBytes($FailureClass)
        if ($payload.Length -gt 96) {
            return
        }
        $stream = [System.IO.FileStream]::new(
            $fullArtifactPath,
            [System.IO.FileMode]::CreateNew,
            [System.IO.FileAccess]::Write,
            [System.IO.FileShare]::Read
        )
        try {
            $stream.Write($payload, 0, $payload.Length)
            $stream.Flush($true)
        }
        finally {
            $stream.Dispose()
        }
    }
    catch {
        # The bounded stdout marker remains available if the optional artifact cannot be created.
    }
}

function Write-FixedStartFailureMarker {
    param(
        [ValidateSet(
            "camera_selection_missing",
            "voicevox_unavailable",
            "required_token_missing_or_short",
            "required_port_conflict",
            "dependency_or_tool_missing",
            "first_service_spawn_failed",
            "stack_start_failed_unknown",
            "stack_preflight_failed",
            "stack_config_preflight_failed",
            "previous_stack_preflight_failed",
            "entrypoint_missing",
            "pid_registry_write_failed"
        )]
        [string]$FailureClass
    )
    if ([string]::IsNullOrWhiteSpace($script:FixedStartFailureClass)) {
        $script:FixedStartFailureClass = $FailureClass
        Write-FixedStartFailureArtifact -FailureClass $FailureClass
        [Console]::Out.WriteLine("SWORD_FIXED_START_FAILURE_CLASS:$FailureClass")
    }
}

function Resolve-StackStateDir {
    param(
        [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
        [string]$StackStateDir = ""
    )
    if ([string]::IsNullOrWhiteSpace($StackStateDir)) {
        $StackStateDir = [Environment]::GetEnvironmentVariable("HOME_CONTROL_STACK_STATE_DIR")
    }
    if ([string]::IsNullOrWhiteSpace($StackStateDir)) {
        return Join-Path $WorkspaceRoot ".cache\home-control-stack"
    }
    if ([System.IO.Path]::IsPathRooted($StackStateDir)) {
        return $StackStateDir
    }
    return Join-Path $WorkspaceRoot $StackStateDir
}

. (Join-Path $PSScriptRoot "resolve-home-control-workspace.ps1")
$WorkspaceRoot = Resolve-HomeControlWorkspaceRoot -WorkspaceRoot $WorkspaceRoot -ScriptRoot $PSScriptRoot
$StackStateDir = Resolve-StackStateDir -WorkspaceRoot $WorkspaceRoot -StackStateDir $StackStateDir

function Resolve-WorkspaceDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
        [Parameter(Mandatory = $true)][string[]]$RelativePaths,
        [Parameter(Mandatory = $true)][string]$FallbackRelativePath
    )

    foreach ($relativePath in $RelativePaths) {
        $candidate = Join-Path $WorkspaceRoot $relativePath
        $resolved = Resolve-Path -LiteralPath $candidate -ErrorAction SilentlyContinue
        if ($null -ne $resolved -and (Test-Path -LiteralPath $resolved.Path -PathType Container)) {
            return $resolved.Path
        }
    }

    return Join-Path $WorkspaceRoot $FallbackRelativePath
}

if ([string]::IsNullOrWhiteSpace($HomeAssistantServerRoot)) {
    $HomeAssistantServerRoot = Join-Path $WorkspaceRoot "organs\action\home-assistant-server"
}
if ([string]::IsNullOrWhiteSpace($MediapipeRoot)) {
    $MediapipeRoot = Join-Path $WorkspaceRoot "organs\reflex\mediapipe-sword-sign"
}
if ([string]::IsNullOrWhiteSpace($VisionSnapshotProcessorRoot)) {
    $VisionSnapshotProcessorRoot = Join-Path $WorkspaceRoot "organs\environment\vision-snapshot-processor"
}
if ([string]::IsNullOrWhiteSpace($AituberRoot)) {
    $AituberRoot = Join-Path $WorkspaceRoot "organs\expression\aituber-kit"
}
if ([string]::IsNullOrWhiteSpace($TouchDesignerGuiRoot)) {
    $TouchDesignerGuiRoot = Join-Path $WorkspaceRoot "organs\display\touchdesigner-ai-controller"
}
if ([string]::IsNullOrWhiteSpace($ThoughtCoreRoot)) {
    $ThoughtCoreRoot = Resolve-WorkspaceDirectory `
        -WorkspaceRoot $WorkspaceRoot `
        -RelativePaths @("control-plane\core") `
        -FallbackRelativePath "control-plane\core"
}
if ([string]::IsNullOrWhiteSpace($EnvironmentStateServerRoot)) {
    $EnvironmentStateServerRoot = Join-Path $WorkspaceRoot "organs\environment\environment-state-server"
}

function Resolve-HomeControlConfigPath {
    param(
        [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
        [Parameter(Mandatory = $true)][string]$HomeAssistantServerRoot,
        [string]$ConfiguredPath = ""
    )

    $localLiveConfigPath = Join-Path $WorkspaceRoot "local\env\home-control.live.yaml"
    if ([string]::IsNullOrWhiteSpace($ConfiguredPath)) {
        if (Test-Path -LiteralPath $localLiveConfigPath -PathType Leaf) {
            return (Resolve-Path -LiteralPath $localLiveConfigPath).Path
        }
        return (Resolve-Path -LiteralPath (Join-Path $HomeAssistantServerRoot "config\home-control.yaml")).Path
    }

    if ([System.IO.Path]::IsPathRooted($ConfiguredPath)) {
        return (Resolve-Path -LiteralPath $ConfiguredPath).Path
    }

    $workspaceRelativePath = Join-Path $WorkspaceRoot $ConfiguredPath
    $workspaceRelativeResolved = Resolve-Path -LiteralPath $workspaceRelativePath -ErrorAction SilentlyContinue
    if ($null -ne $workspaceRelativeResolved) {
        return $workspaceRelativeResolved.Path
    }
    return (Resolve-Path -LiteralPath $ConfiguredPath).Path
}

function Assert-HomeControlConfigNotDemoLiveMapping {
    param(
        [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
        [Parameter(Mandatory = $true)][string]$ConfigPath
    )

    $localLiveConfigPath = Join-Path $WorkspaceRoot "local\env\home-control.live.yaml"
    $localLiveResolved = Resolve-Path -LiteralPath $localLiveConfigPath -ErrorAction SilentlyContinue
    if ($null -eq $localLiveResolved) {
        return
    }
    if ($ConfigPath -ieq $localLiveResolved.Path) {
        return
    }
    $configText = Get-Content -LiteralPath $ConfigPath -Raw -Encoding UTF8
    if ($configText -match 'script\.demo_light_(on|off)') {
        throw (
            "Home Control live config exists, but the selected bridge config still maps light actions to demo scripts. " +
            "Pass -HomeControlConfigPath local\env\home-control.live.yaml or restart the stack through Launch Manager with StopExisting enabled."
        )
    }
}
try {
    $HomeControlConfigPath = Resolve-HomeControlConfigPath `
        -WorkspaceRoot $WorkspaceRoot `
        -HomeAssistantServerRoot $HomeAssistantServerRoot `
        -ConfiguredPath $HomeControlConfigPath
    if (-not $SkipHomeAssistantBridge) {
        Assert-HomeControlConfigNotDemoLiveMapping -WorkspaceRoot $WorkspaceRoot -ConfigPath $HomeControlConfigPath
        $localLiveConfigPath = Join-Path $WorkspaceRoot "local\env\home-control.live.yaml"
        $localLiveResolved = Resolve-Path -LiteralPath $localLiveConfigPath -ErrorAction SilentlyContinue
        $homeControlConfigLabel = $HomeControlConfigPath
        if ($null -ne $localLiveResolved -and $HomeControlConfigPath -ieq $localLiveResolved.Path) {
            $homeControlConfigLabel = "local/env/home-control.live.yaml"
        }
        Write-Host "[home_assistant_bridge] config: $homeControlConfigLabel"
    }
}
catch {
    Write-FixedStartFailureMarker -FailureClass "stack_config_preflight_failed"
    throw
}
$HomeAssistantEnvPath = Join-Path $HomeAssistantServerRoot ".env"
$TouchDesignerGuiToolsRoot = Join-Path $TouchDesignerGuiRoot "tools"
$ThoughtCoreScript = Join-Path $ThoughtCoreRoot "scripts\start-thought-core.ps1"
$ThoughtCoreWatchScript = Join-Path $ThoughtCoreRoot "scripts\start-thought-core-watch.ps1"
$ThoughtCoreEnvPath = Join-Path $ThoughtCoreRoot ".env"
$AiTalkCoreRoot = Resolve-WorkspaceDirectory `
    -WorkspaceRoot $WorkspaceRoot `
    -RelativePaths @("organs\speech-input\ai-talk-core") `
    -FallbackRelativePath "organs\speech-input\ai-talk-core"
$testLaunchVisionSnapshotWithoutMediapipe = (
    $env:NODE_ENV -eq "test" -and
    $env:HOME_CONTROL_STACK_TEST_LAUNCH_VSP_WITHOUT_MEDIAPIPE -eq "true"
)
$LaunchVisionSnapshotProcessor = (
    (-not $SkipVisionSnapshotProcessor) -and
    ((-not $SkipMediapipe -and $MediapipeMode -eq "mediamtx") -or $testLaunchVisionSnapshotWithoutMediapipe)
)

$StateDir = $StackStateDir
$LogDir = Join-Path $StateDir "logs"
$PidFile = Join-Path $StateDir "pids.json"
$StopScript = Join-Path $PSScriptRoot "stop-home-control-stack.ps1"
$ThoughtCoreStatusDir = Join-Path $StateDir "thought-core-api"
$ThoughtCoreControllerManifestPath = Join-Path $ThoughtCoreStatusDir "controller.json"
$ThoughtCoreWatchStatusDir = Join-Path $StateDir "thought-core-watcher"
$HomeAssistantBridgeClientHost = if ($HomeAssistantBridgeHost -eq "0.0.0.0") { "127.0.0.1" } else { $HomeAssistantBridgeHost }
$EnvironmentStateClientHost = if ($EnvironmentStateHost -eq "0.0.0.0") { "127.0.0.1" } else { $EnvironmentStateHost }
$AituberClientHost = if ($AituberHost -eq "0.0.0.0") { "127.0.0.1" } else { $AituberHost }
$TouchDesignerGuiClientHost = if ($TouchDesignerGuiHost -eq "0.0.0.0") { "127.0.0.1" } else { $TouchDesignerGuiHost }
$TouchDesignerUdpClientHost = if ($TouchDesignerUdpHost -eq "0.0.0.0") { "127.0.0.1" } else { $TouchDesignerUdpHost }
$ThoughtCoreClientHost = if ($ThoughtCoreHost -eq "0.0.0.0") { "127.0.0.1" } else { $ThoughtCoreHost }
$ThoughtCoreBaseUrl = "http://{0}:{1}" -f $ThoughtCoreClientHost, $ThoughtCorePort
$OpenAIBrokerHost = "127.0.0.1"
$OpenAIBrokerBaseUrl = "http://{0}:{1}/v1" -f $OpenAIBrokerHost, $OpenAIBrokerPort
$OpenAIBrokerHealthUrl = "http://{0}:{1}/health" -f $OpenAIBrokerHost, $OpenAIBrokerPort
$BrokerRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..")).Path
$AituberProjectionVisualUrl = "http://{0}:{1}/projection-visual/?mode=passive&hud=0" -f $AituberClientHost, $AituberPort
$MediapipeCameraHubChildProcessFile = Join-Path $StateDir "modules\mediapipe_camera_hub_stack\processes.json"
$StateQueryFeedbackPath = Join-Path $StateDir "feedback\state-query.jsonl"

$ExternalProcessDenyList = @(
    "chrome",
    "msedge",
    "firefox",
    "brave",
    "brave-browser",
    "opera",
    "vivaldi",
    "updater",
    "googleupdate",
    "microsoftedgeupdate"
)

function Resolve-Tool {
    param([Parameter(Mandatory = $true)][string]$Name)
    try {
        if ($Name -eq "npm") {
            $npmCmd = Get-Command "npm.cmd" -ErrorAction SilentlyContinue
            if ($null -ne $npmCmd) {
                return $npmCmd.Source
            }
        }
        $command = Get-Command $Name -ErrorAction Stop
        if ($command.Source -like "*.ps1") {
            $cmdCommand = Get-Command "$Name.cmd" -ErrorAction SilentlyContinue
            if ($null -ne $cmdCommand) {
                return $cmdCommand.Source
            }
        }
        return $command.Source
    }
    catch {
        Write-FixedStartFailureMarker -FailureClass "dependency_or_tool_missing"
        throw
    }
}

function Resolve-CurrentPowerShell {
    $currentProcess = Get-Process -Id $PID -ErrorAction SilentlyContinue
    if ($null -ne $currentProcess -and -not [string]::IsNullOrWhiteSpace($currentProcess.Path)) {
        return $currentProcess.Path
    }
    $pwsh = Get-Command "pwsh" -ErrorAction SilentlyContinue
    if ($null -ne $pwsh) {
        return $pwsh.Source
    }
    return (Get-Command "powershell" -ErrorAction Stop).Source
}

function Assert-Directory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Label
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Container)) {
        Write-FixedStartFailureMarker -FailureClass "dependency_or_tool_missing"
        Write-FixedStartFailureMarker -FailureClass "stack_preflight_failed"
        throw "$Label directory not found: $Path"
    }
}

function Test-HttpReachable {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 2
    )
    try {
        Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds | Out-Null
        return $true
    }
    catch {
        return $false
    }
}

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match "^\s*#") {
            continue
        }
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)\s*$") {
            $value = $matches[1].Trim()
            if (
                $value.Length -ge 2 -and
                (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                 ($value.StartsWith("'") -and $value.EndsWith("'")))
            ) {
                return $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return ""
}

function Assert-VoicevoxReady {
    param(
        [Parameter(Mandatory = $true)][string]$BaseUrl,
        [int]$TimeoutSeconds = 45
    )
    if ($DryRun) {
        Write-Host "[voicevox] dry-run: VOICEVOX readiness check skipped."
        return
    }

    $normalizedBaseUrl = $BaseUrl.TrimEnd("/")
    $versionUrl = "$normalizedBaseUrl/version"
    $readinessHelper = Join-Path $WorkspaceRoot "scripts\check-voicevox-readiness.ps1"
    if (Test-Path -LiteralPath $readinessHelper -PathType Leaf) {
        try {
            $helperOutput = & $readinessHelper `
                -EndpointUrl $versionUrl `
                -StartIfNeeded `
                -TimeoutSeconds $TimeoutSeconds `
                -PollSeconds 1 `
                -Json
            $helperResult = $helperOutput | ConvertFrom-Json
            $classification = [string]$helperResult.classification
            $endpointInitial = [string]$helperResult.endpoint_initial
            $endpointAfterStart = [string]$helperResult.endpoint_after_start
            $startAttempted = [bool]$helperResult.start_attempted
            if ($classification -eq "pass") {
                if ($startAttempted) {
                    Write-Host "[voicevox] started existing local VOICEVOX and reached $normalizedBaseUrl"
                }
                else {
                    Write-Host "[voicevox] reachable: $normalizedBaseUrl"
                }
                return
            }
            throw @"
VOICEVOX is not reachable, so AITuber voice output will fail.

The startup helper checked the existing local VOICEVOX app but could not make the endpoint ready.

Next action:
  Start VOICEVOX manually, wait until the engine is ready, then rerun:
    .\start-home-control-stack.bat -StopExisting

Diagnostics:
  checked URL: $versionUrl
  readiness timeout seconds: $TimeoutSeconds
  endpoint before helper: $endpointInitial
  endpoint after helper: $endpointAfterStart
  start attempted: $startAttempted
  helper classification: $classification
  note: $($helperResult.note)

If you intentionally do not use VOICEVOX, start this script with -SkipVoicevoxCheck.
"@
        }
        catch {
            if ($_.Exception.Message -match "^VOICEVOX is not reachable") {
                throw
            }
            throw @"
VOICEVOX readiness helper failed, so AITuber voice output may fail.

Next action:
  Start VOICEVOX manually, wait until the engine is ready, then rerun:
    .\start-home-control-stack.bat -StopExisting

Diagnostics:
  checked URL: $versionUrl
  helper: $readinessHelper
  readiness timeout seconds: $TimeoutSeconds
  error: $($_.Exception.Message)

If you intentionally do not use VOICEVOX, start this script with -SkipVoicevoxCheck.
"@
        }
    }

    try {
        $response = Invoke-WebRequest -Uri $versionUrl -UseBasicParsing -TimeoutSec 2
        $version = ($response.Content | Out-String).Trim().Trim('"')
        if ([string]::IsNullOrWhiteSpace($version)) {
            Write-Host "[voicevox] reachable: $normalizedBaseUrl"
        }
        else {
            Write-Host "[voicevox] reachable: $normalizedBaseUrl (version $version)"
        }
    }
    catch {
        $voicevoxProcesses = @(Get-Process -ErrorAction SilentlyContinue | Where-Object {
            $_.ProcessName -match "voicevox"
        })
        $processState = if ($voicevoxProcesses.Count -gt 0) { "running" } else { "not found" }
        throw @"
VOICEVOX is not reachable, so AITuber voice output will fail.

Start VOICEVOX and wait until the engine is ready, then rerun:
  .\start-home-control-stack.bat -StopExisting

Diagnostics:
  checked URL: $versionUrl
  VOICEVOX process: $processState
  error: $($_.Exception.Message)

If you intentionally do not use VOICEVOX, start this script with -SkipVoicevoxCheck.
"@
    }
}

function Assert-HomeControlBridgeTokenConfigured {
    param([Parameter(Mandatory = $true)][string]$EnvPath)
    if ($DryRun) {
        Write-Host "[home_assistant_bridge] dry-run: HOME_CONTROL_API_TOKEN check skipped."
        return
    }

    $token = Get-DotEnvValue -Path $EnvPath -Name "HOME_CONTROL_API_TOKEN"
    if ([string]::IsNullOrWhiteSpace($token)) {
        throw @"
HOME_CONTROL_API_TOKEN is missing for home_assistant_bridge.

Set a random 32+ character token in:
  $EnvPath

Then set the same value wherever a local caller needs bridge access:
  HOME_CONTROL_API_TOKEN

This token is for local services that call the Home Assistant bridge.
"@
    }
    if ($token.Trim().Length -lt 32) {
        throw @"
HOME_CONTROL_API_TOKEN is too short for home_assistant_bridge.

Use a random 32+ character token in:
  $EnvPath

Then set the same value wherever a local caller needs bridge access:
  HOME_CONTROL_API_TOKEN
"@
    }
    Write-Host "[home_assistant_bridge] HOME_CONTROL_API_TOKEN present in $EnvPath (value hidden)"
}

function Assert-EnvironmentStateTokenConfigured {
    param([Parameter(Mandatory = $true)][string]$EnvPath)
    if ($DryRun) {
        Write-Host "[environment_state_server] dry-run: API token check skipped."
        return
    }

    $token = [Environment]::GetEnvironmentVariable("ENVIRONMENT_API_TOKEN")
    if ([string]::IsNullOrWhiteSpace($token)) {
        $token = Get-DotEnvValue -Path $EnvPath -Name "ENVIRONMENT_API_TOKEN"
    }
    if ([string]::IsNullOrWhiteSpace($token)) {
        $token = [Environment]::GetEnvironmentVariable("HOME_CONTROL_API_TOKEN")
    }
    if ([string]::IsNullOrWhiteSpace($token)) {
        $token = Get-DotEnvValue -Path $EnvPath -Name "HOME_CONTROL_API_TOKEN"
    }
    if ([string]::IsNullOrWhiteSpace($token)) {
        throw @"
ENVIRONMENT_API_TOKEN or HOME_CONTROL_API_TOKEN is missing for environment_state_server.

Set ENVIRONMENT_API_TOKEN or reuse HOME_CONTROL_API_TOKEN in:
  $EnvPath

Local callers should call GET /environment/current with:
  Authorization: Bearer <token>
"@
    }
    if ($token.Trim().Length -lt 32) {
        throw @"
Environment API token is too short.

Use a random 32+ character token in ENVIRONMENT_API_TOKEN or HOME_CONTROL_API_TOKEN:
  $EnvPath
"@
    }
    Write-Host "[environment_state_server] API token present in $EnvPath or process env (value hidden)"
}

function Get-HomeControlFaultConfigSummary {
    param([Parameter(Mandatory = $true)][string]$ConfigPath)
    $summary = [ordered]@{
        HasFaults = $false
        Enabled = $false
        RuleCount = 0
    }
    if (-not (Test-Path -LiteralPath $ConfigPath -PathType Leaf)) {
        return [pscustomobject]$summary
    }

    $inFaults = $false
    foreach ($line in Get-Content -LiteralPath $ConfigPath -Encoding UTF8) {
        if ($line -match "^\s*faults\s*:\s*$") {
            $inFaults = $true
            $summary.HasFaults = $true
            continue
        }
        if ($inFaults -and $line -match "^\S") {
            break
        }
        if (-not $inFaults) {
            continue
        }
        if ($line -match "^\s+enabled\s*:\s*true\s*(#.*)?$") {
            $summary.Enabled = $true
        }
        if ($line -match "^\s+scenario\s*:") {
            $summary.RuleCount += 1
        }
    }
    return [pscustomobject]$summary
}

function Report-HomeControlFaultInjectionStatus {
    param(
        [Parameter(Mandatory = $true)][string]$EnvPath,
        [Parameter(Mandatory = $true)][string]$ConfigPath,
        [switch]$FaultModeOverride
    )
    $mode = (Get-DotEnvValue -Path $EnvPath -Name "HOME_CONTROL_FAULT_MODE").Trim().ToLowerInvariant()
    $modeEnabled = $FaultModeOverride -or (@("1", "true", "yes", "on") -contains $mode)
    $summary = Get-HomeControlFaultConfigSummary -ConfigPath $ConfigPath

    if (-not $modeEnabled) {
        if ($summary.Enabled) {
            Write-Host "[home_assistant_bridge] fault injection rules configured but HOME_CONTROL_FAULT_MODE is off"
        }
        else {
            Write-Host "[home_assistant_bridge] fault injection disabled"
        }
        return
    }

    if (-not $summary.Enabled) {
        Write-Warning @"
HOME_CONTROL_FAULT_MODE is on, but fault injection is not enabled in config.

Enable faults.enabled and add rules in:
  $ConfigPath

Fault injection requires both config faults.enabled=true and HOME_CONTROL_FAULT_MODE=1.
"@
        return
    }

    $source = if ($FaultModeOverride) { "process override" } else { "env file" }
    Write-Host "[home_assistant_bridge] fault injection enabled: rules=$($summary.RuleCount) ($source)"
}

function Get-ListeningPortOwner {
    param([Parameter(Mandatory = $true)][int]$Port)
    return @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
}

function Get-ProcessCommandLine {
    param([Parameter(Mandatory = $true)][int]$ProcessId)
    try {
        $process = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction Stop
        return [string]$process.CommandLine
    }
    catch {
        return ""
    }
}

function Test-CommandLineReferencesPath {
    param(
        [string]$CommandLine,
        [string]$Path
    )
    if ([string]::IsNullOrWhiteSpace($CommandLine) -or [string]::IsNullOrWhiteSpace($Path)) {
        return $false
    }
    try {
        $resolvedPath = (Resolve-Path -LiteralPath $Path -ErrorAction Stop).Path
    }
    catch {
        $resolvedPath = $Path
    }
    $needle = $resolvedPath.TrimEnd("\", "/")
    if ([string]::IsNullOrWhiteSpace($needle)) {
        return $false
    }
    return $CommandLine.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0
}

function Get-PortConflicts {
    param(
        [Parameter(Mandatory = $true)][object[]]$PortSpecs
    )

    $conflicts = @()
    foreach ($spec in $PortSpecs) {
        $owners = @(Get-ListeningPortOwner -Port ([int]$spec.Port))
        foreach ($owner in $owners) {
            $pidValue = [int]$owner.OwningProcess
            if ($pidValue -le 0 -or $pidValue -eq $PID) {
                continue
            }
            $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
            $conflicts += [pscustomobject]@{
                Label = [string]$spec.Label
                Port = [int]$spec.Port
                PID = $pidValue
                ProcessName = if ($null -ne $process) { $process.ProcessName } else { "" }
                CommandLine = Get-ProcessCommandLine -ProcessId $pidValue
            }
        }
    }
    return @(
        $conflicts |
            Sort-Object Port, PID -Unique
    )
}

function Test-ExternalProcessDenied {
    param([string]$ProcessName)
    $normalized = Normalize-ProcessName -Name $ProcessName
    if ([string]::IsNullOrWhiteSpace($normalized)) {
        return $false
    }
    return $ExternalProcessDenyList -contains $normalized
}

function Get-ReclaimableRootForPortConflict {
    param([Parameter(Mandatory = $true)][object]$Conflict)
    if (Test-ExternalProcessDenied -ProcessName ([string]$Conflict.ProcessName)) {
        return ""
    }
    $root = switch ([string]$Conflict.Label) {
        "home-assistant-server" { $HomeAssistantServerRoot; break }
        "environment-state-server" { $EnvironmentStateServerRoot; break }
        "mediapipe-sword-sign" { $MediapipeRoot; break }
        "vision-snapshot-processor" { $VisionSnapshotProcessorRoot; break }
        "AITuber Kit" { $AituberRoot; break }
        "TouchDesigner control GUI" { $TouchDesignerGuiRoot; break }
        "thought-core" { $ThoughtCoreRoot; break }
        default { "" }
    }
    if (Test-CommandLineReferencesPath -CommandLine ([string]$Conflict.CommandLine) -Path $root) {
        return $root
    }
    return ""
}

function Stop-ReclaimablePortConflicts {
    param([Parameter(Mandatory = $true)][object[]]$Conflicts)
    $reclaimed = @()
    foreach ($conflict in $Conflicts) {
        $root = Get-ReclaimableRootForPortConflict -Conflict $conflict
        if ([string]::IsNullOrWhiteSpace($root)) {
            continue
        }
        Write-Host (
            "[ports] reclaiming stale managed port owner: {0}:{1} PID {2} {3}" -f
            $conflict.Label,
            $conflict.Port,
            $conflict.PID,
            $conflict.ProcessName
        )
        try {
            Stop-Process -Id ([int]$conflict.PID) -Force -ErrorAction Stop
            Wait-Process -Id ([int]$conflict.PID) -Timeout 5 -ErrorAction SilentlyContinue
            $reclaimed += $conflict
        }
        catch {
            Write-Warning (
                "Failed to reclaim stale managed port owner {0}:{1} PID {2}: {3}" -f
                $conflict.Label,
                $conflict.Port,
                $conflict.PID,
                $_.Exception.Message
            )
        }
    }
    return @($reclaimed)
}

function Normalize-ProcessName {
    param([string]$Name)
    if ([string]::IsNullOrWhiteSpace($Name)) {
        return ""
    }
    $normalized = $Name.ToLowerInvariant()
    if ($normalized.EndsWith(".exe")) {
        $normalized = $normalized.Substring(0, $normalized.Length - 4)
    }
    return $normalized
}

function Get-ObjectProperty {
    param(
        [object]$Object,
        [Parameter(Mandatory = $true)][string]$Name,
        [object]$Default = $null
    )
    if ($null -eq $Object) {
        return $Default
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) {
        return $Default
    }
    return $property.Value
}

function ConvertTo-StringArray {
    param([object]$Value)
    if ($null -eq $Value) {
        return @()
    }
    return @($Value | ForEach-Object { [string]$_ } | Where-Object { -not [string]::IsNullOrWhiteSpace($_) })
}

function Test-ProcessStartTimeMatches {
    param(
        [Parameter(Mandatory = $true)][object]$Process,
        [string]$RecordedAt,
        [int]$GraceSeconds = 60
    )
    if ([string]::IsNullOrWhiteSpace($RecordedAt)) {
        return $true
    }
    try {
        $recorded = [DateTimeOffset]::Parse($RecordedAt)
        $processStarted = [DateTimeOffset]$Process.StartTime
        return (
            $processStarted -ge $recorded.AddSeconds(-10) -and
            $processStarted -le $recorded.AddSeconds($GraceSeconds)
        )
    }
    catch {
        return $false
    }
}

function Test-RecordedProcessEntryAlive {
    param([Parameter(Mandatory = $true)][object]$Entry)
    $pidValue = [int](Get-ObjectProperty -Object $Entry -Name "pid" -Default 0)
    if ($pidValue -le 0) {
        return $false
    }
    $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
    if ($null -eq $process) {
        return $false
    }
    $startedAt = [string](Get-ObjectProperty -Object $Entry -Name "started_at" -Default "")
    if (-not (Test-ProcessStartTimeMatches -Process $process -RecordedAt $startedAt -GraceSeconds 60)) {
        return $false
    }
    $allowedNames = @(ConvertTo-StringArray -Value (Get-ObjectProperty -Object $Entry -Name "allowed_process_names" -Default @()))
    if ($allowedNames.Count -eq 0) {
        return -not (Test-ExternalProcessDenied -ProcessName ([string]$process.ProcessName))
    }
    $processName = Normalize-ProcessName -Name ([string]$process.ProcessName)
    $allowed = @($allowedNames | ForEach-Object { Normalize-ProcessName -Name $_ })
    return $allowed -contains $processName
}

function Resolve-PortConflicts {
    param(
        [Parameter(Mandatory = $true)][object[]]$PortSpecs
    )

    $conflicts = @(Get-PortConflicts -PortSpecs $PortSpecs)
    if ($conflicts.Count -eq 0) {
        return
    }
    if ((-not $DryRun) -and $StopExisting) {
        $reclaimed = @(Stop-ReclaimablePortConflicts -Conflicts $conflicts)
        if ($reclaimed.Count -gt 0) {
            Start-Sleep -Seconds 1
            $conflicts = @(Get-PortConflicts -PortSpecs $PortSpecs)
            if ($conflicts.Count -eq 0) {
                Write-Host "[ports] stale managed port owners reclaimed; continuing startup."
                return
            }
        }
    }

    Write-Host "Processes are already using required ports:"
    $conflicts |
        Select-Object Label, Port, PID, ProcessName |
        Format-Table -AutoSize |
        Out-String -Width 240 |
        Write-Host

    $summary = @($conflicts | ForEach-Object { "$($_.Label):$($_.Port) PID $($_.PID) $($_.ProcessName)" }) -join ", "
    if ($DryRun) {
        throw "Required ports are in use. DryRun will not stop unrecorded processes: $summary"
    }
    throw (
        "Required ports are still in use after recorded stack cleanup. " +
        "These processes are outside the current Home Control process registry, " +
        "so they were not stopped automatically: $summary"
    )
}

function Read-PidState {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $PidFile -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Read-JsonFile {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        return $null
    }
}

function Test-PathUnderDirectory {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Directory
    )
    try {
        $fullPath = [System.IO.Path]::GetFullPath($Path)
        $fullDirectory = [System.IO.Path]::GetFullPath($Directory).TrimEnd([System.IO.Path]::DirectorySeparatorChar, [System.IO.Path]::AltDirectorySeparatorChar)
        return $fullPath.StartsWith($fullDirectory + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
    }
    catch {
        return $false
    }
}

function Clear-ChildProcessManifest {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }
    if (-not (Test-PathUnderDirectory -Path $Path -Directory $StateDir)) {
        throw "Refusing to clear child process manifest outside state dir: $Path"
    }
    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Remove-Item -LiteralPath $Path -Force
    }
}

function Test-ManifestFreshForChild {
    param(
        [Parameter(Mandatory = $true)][object]$Manifest,
        [Parameter(Mandatory = $true)][object]$Child
    )
    $updatedAt = [string](Get-ObjectProperty -Object $Manifest -Name "updated_at" -Default "")
    if ([string]::IsNullOrWhiteSpace($updatedAt)) {
        return $false
    }
    try {
        $manifestTime = [DateTimeOffset]::Parse($updatedAt)
        $childStarted = [DateTimeOffset]::Parse([string]$Child.StartedAt)
        return $manifestTime -ge $childStarted.AddSeconds(-2)
    }
    catch {
        return $false
    }
}

function Test-CameraHubRequiredProcessesRunning {
    param([Parameter(Mandatory = $true)][object]$Manifest)
    $runningByName = @{}
    foreach ($entry in @(Get-ObjectProperty -Object $Manifest -Name "processes" -Default @())) {
        $name = [string](Get-ObjectProperty -Object $entry -Name "name" -Default "")
        $running = Get-ObjectProperty -Object $entry -Name "running" -Default $null
        if ([string]::IsNullOrWhiteSpace($name) -or $running -isnot [bool] -or $runningByName.ContainsKey($name)) {
            return $false
        }
        $runningByName[$name] = $running
    }
    return (
        $runningByName.ContainsKey("mediamtx") -and $runningByName["mediamtx"] -and
        $runningByName.ContainsKey("camera-hub") -and $runningByName["camera-hub"]
    )
}

function Test-CameraHubRuntimeOwnership {
    param(
        [Parameter(Mandatory = $true)][object]$Manifest,
        [Parameter(Mandatory = $true)][object]$Child,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $ownerPid = 0
    if (-not [int]::TryParse([string](Get-ObjectProperty -Object $Manifest -Name "owner_pid" -Default ""), [ref]$ownerPid) -or $ownerPid -le 0) {
        return $false
    }
    $rootPid = [int]$Child.Process.Id
    $root = Get-Process -Id $rootPid -ErrorAction SilentlyContinue
    $owner = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
    $ownerIdentity = Get-CimInstance Win32_Process -Filter "ProcessId = $ownerPid" -ErrorAction SilentlyContinue
    if (
        $null -eq $root -or $null -eq $owner -or $null -eq $ownerIdentity -or
        -not (Test-ProcessStartTimeMatches -Process $root -RecordedAt ([string]$Child.StartedAt) -GraceSeconds 60) -or
        (Normalize-ProcessName -Name ([string]$owner.ProcessName)) -ne "python"
    ) {
        return $false
    }
    $rootDescendants = @(Get-DescendantProcessIds -RootProcessId $rootPid)
    if ($rootDescendants -notcontains $ownerPid) {
        return $false
    }
    try {
        if ([DateTimeOffset]$owner.StartTime -lt ([DateTimeOffset]$root.StartTime).AddSeconds(-2)) {
            return $false
        }
    }
    catch {
        return $false
    }

    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue |
        Where-Object { [string]$_.LocalAddress -in @("127.0.0.1", "::1") } |
        Select-Object -ExpandProperty OwningProcess -Unique)
    if ($listeners.Count -ne 1) {
        return $false
    }
    $listenerPid = [int]$listeners[0]
    if (@(Get-DescendantProcessIds -RootProcessId $ownerPid) -notcontains $listenerPid) {
        return $false
    }
    $listener = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
    $listenerIdentity = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid" -ErrorAction SilentlyContinue
    if ($null -eq $listener -or $null -eq $listenerIdentity) {
        return $false
    }
    $commandLine = [string]$listenerIdentity.CommandLine
    return (
        (Normalize-ProcessName -Name ([string]$listener.ProcessName)) -eq "python" -and
        $commandLine -match '(?i)apps[\\/]serve_camera_hub\.py'
    )
}

function Get-ValidatedCameraHubState {
    param(
        [Parameter(Mandatory = $true)][object]$Manifest,
        [Parameter(Mandatory = $true)][object]$Child
    )
    $invalid = {
        param([string]$Class)
        return [pscustomobject]@{
            Valid = $false
            Operational = $false
            StateClass = "unknown"
            ValidationClass = $Class
        }
    }

    if ((Get-ObjectProperty -Object $Manifest -Name "schema_version" -Default 0) -ne 2) {
        return & $invalid "camera_manifest_schema_invalid"
    }
    if (
        [string](Get-ObjectProperty -Object $Manifest -Name "module" -Default "") -ne "mediapipe-sword-sign" -or
        [string](Get-ObjectProperty -Object $Manifest -Name "service" -Default "") -ne "mediapipe_camera_hub_stack"
    ) {
        return & $invalid "camera_manifest_authority_invalid"
    }

    $stateClass = [string](Get-ObjectProperty -Object $Manifest -Name "camera_state_class" -Default "")
    $allowedClasses = @("starting", "unavailable", "recovering", "ready", "stopping")
    if ($stateClass -notin $allowedClasses) {
        return & $invalid "camera_manifest_state_invalid"
    }

    $readyValue = Get-ObjectProperty -Object $Manifest -Name "ready" -Default $null
    if ($readyValue -isnot [bool]) {
        return & $invalid "camera_manifest_ready_invalid"
    }
    $readyAt = [string](Get-ObjectProperty -Object $Manifest -Name "ready_at" -Default "")
    $readyAtPresent = -not [string]::IsNullOrWhiteSpace($readyAt)
    if (($readyValue -ne ($stateClass -eq "ready")) -or ($readyValue -ne $readyAtPresent)) {
        return & $invalid "camera_manifest_ready_mismatch"
    }
    if ($readyAtPresent) {
        try { [void][DateTimeOffset]::Parse($readyAt) }
        catch { return & $invalid "camera_manifest_ready_at_invalid" }
    }
    if (-not (Test-ManifestFreshForChild -Manifest $Manifest -Child $Child)) {
        return & $invalid "camera_manifest_lineage_stale"
    }
    if (-not (Test-CameraHubRuntimeOwnership -Manifest $Manifest -Child $Child -Port $MediapipePort)) {
        return & $invalid "camera_manifest_runtime_ownership_invalid"
    }

    $operational = (
        $stateClass -in @("unavailable", "recovering", "ready") -and
        (Test-CameraHubRequiredProcessesRunning -Manifest $Manifest)
    )
    return [pscustomobject]@{
        Valid = $true
        Operational = $operational
        StateClass = $stateClass
        ValidationClass = "camera_manifest_valid"
    }
}

function Wait-CameraHubStackReady {
    param(
        [Parameter(Mandatory = $true)][object]$Child,
        [int]$TimeoutSeconds = 35
    )
    $manifestPath = $MediapipeCameraHubChildProcessFile
    if ([string]::IsNullOrWhiteSpace($manifestPath)) {
        return
    }
    $configuredManifestPath = [string]$Child.ChildProcessFile
    if (
        [string]::IsNullOrWhiteSpace($configuredManifestPath) -or
        -not [System.IO.Path]::GetFullPath($configuredManifestPath).Equals(
            [System.IO.Path]::GetFullPath($manifestPath),
            [System.StringComparison]::OrdinalIgnoreCase
        )
    ) {
        throw "$($Child.Name) Camera Hub manifest route does not match the canonical state path."
    }

    Write-Host "[$($Child.Name)] waiting for Camera Hub operational state..."
    $deadline = [DateTimeOffset]::Now.AddSeconds($TimeoutSeconds)
    $lastClass = "camera_manifest_waiting"
    while ([DateTimeOffset]::Now -lt $deadline) {
        if ($Child.Process.HasExited) {
            throw "$($Child.Name) exited before Camera Hub became operational. Last class: $lastClass"
        }

        $manifest = Read-JsonFile -Path $manifestPath
        if ($null -ne $manifest) {
            $cameraState = Get-ValidatedCameraHubState -Manifest $manifest -Child $Child
            $lastClass = if ($cameraState.Valid) {
                "camera_state_$($cameraState.StateClass)"
            }
            else {
                $cameraState.ValidationClass
            }
            if ($cameraState.Operational) {
                Write-Host "[$($Child.Name)] Camera Hub operational: camera_state_$($cameraState.StateClass)"
                return $cameraState.StateClass
            }
        }

        Start-Sleep -Milliseconds 500
    }

    throw "$($Child.Name) did not report a valid operational Camera Hub state within ${TimeoutSeconds}s. Last class: $lastClass"
}

function Test-RecordedProcessesAlive {
    $state = Read-PidState
    if ($null -eq $state -or $null -eq $state.processes) {
        return $false
    }
    foreach ($entry in $state.processes) {
        if (Test-RecordedProcessEntryAlive -Entry $entry) {
            return $true
        }
    }
    return $false
}

function Save-PidState {
    param([Parameter(Mandatory = $true)][object[]]$Children)
    New-Item -ItemType Directory -Force -Path $StateDir | Out-Null
    $state = [pscustomobject]@{
        schema_version = 3
        started_at = [DateTimeOffset]::Now.ToString("o")
        workspace_root = $WorkspaceRoot
        processes = @(
            $Children | ForEach-Object {
                $entry = [ordered]@{
                    name = $_.Name
                    module = $_.Module
                    role = $_.Role
                    pid = $_.Process.Id
                    working_directory = $_.WorkingDirectory
                    command = $_.CommandLine
                    started_at = $_.StartedAt
                    stop_strategy = $_.StopStrategy
                    allowed_process_names = @($_.AllowedProcessNames)
                    child_process_file = $_.ChildProcessFile
                }
                if (
                    -not [string]::IsNullOrWhiteSpace([string]$_.OwnershipClass) -or
                    [string]$_.StopStrategy -ceq "role_scoped_descendant"
                ) {
                    $entry["ownership_class"] = [string]$_.OwnershipClass
                    $entry["ownership_root_pid"] = [int]$_.OwnershipRootPid
                    $entry["ownership_root_started_at"] = [string]$_.OwnershipRootStartedAt
                    $entry["ownership_parent_pid"] = [int]$_.OwnershipParentPid
                    $entry["ownership_lineage"] = @($_.OwnershipLineage)
                    $entry["ownership_seal"] = [string]$_.OwnershipSeal
                    $entry["expected_module"] = [string]$_.ExpectedModule
                    $entry["expected_port"] = [int]$_.ExpectedPort
                }
                [pscustomobject]$entry
            }
        )
    }
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $PidFile -Encoding UTF8
}

function Save-StartupPidState {
    param([Parameter(Mandatory = $true)][object[]]$Children)
    try {
        Save-PidState -Children $Children
    }
    catch {
        Write-FixedStartFailureMarker -FailureClass "pid_registry_write_failed"
        throw
    }
}

function Assert-SelectedServiceEntrypoints {
    param([Parameter(Mandatory = $true)][object[]]$Specs)
    foreach ($spec in $Specs) {
        if ([string]::IsNullOrWhiteSpace([string]$spec.FilePath) -or -not (Test-Path -LiteralPath $spec.FilePath -PathType Leaf)) {
            Write-FixedStartFailureMarker -FailureClass "entrypoint_missing"
            throw "Selected service entrypoint is unavailable: $($spec.Name)"
        }
    }
}

function Format-CommandLine {
    param([Parameter(Mandatory = $true)][string[]]$Command)
    return ($Command | ForEach-Object {
        if ($_ -match "[\s`"]") {
            '"' + ($_ -replace '"', '\"') + '"'
        }
        else {
            $_
        }
    }) -join " "
}

function Protect-CameraSelectionCommand {
    param([Parameter(Mandatory = $true)][string[]]$Command)
    $protected = @($Command)
    for ($index = 0; $index -lt $protected.Count; $index++) {
        if (
            $protected[$index] -in @("-MediapipeCameraName", "--camera-name") -and
            $index + 1 -lt $protected.Count
        ) {
            $protected[$index + 1] = "<local-camera-selection>"
            $index++
        }
    }
    return [string[]]$protected
}

function Write-GuideItem {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Target,
        [Parameter(Mandatory = $true)][string]$Description
    )
    Write-Host ("  {0}" -f $Name)
    Write-Host ("    Target: {0}" -f $Target)
    Write-Host ("    Use   : {0}" -f $Description)
}

function Write-StackEndpointGuide {
    Write-Host ""
    Write-Host "Home Control Stack is starting in this terminal."
    Write-Host ""
    Write-Host "Open in browser"
    Write-Host "---------------"
    if (-not $SkipAituber) {
        Write-GuideItem `
            -Name "AITuber Kit" `
            -Target ("http://{0}:{1}" -f $AituberClientHost, $AituberPort) `
            -Description "会話入力、AITuber Kit の通常画面。"
        Write-GuideItem `
            -Name "Projection Visual" `
            -Target ("http://{0}:{1}/projection-visual/" -f $AituberClientHost, $AituberPort) `
            -Description "投影・配信用のキャラクター表示画面。普段見るメインの表示はこちら。"
        Write-GuideItem `
            -Name "Projection Visual passive" `
            -Target ("http://{0}:{1}/projection-visual/?mode=passive" -f $AituberClientHost, $AituberPort) `
            -Description "投影先・TouchDesigner プレビュー向けの passive 表示。操作 UI を前面に出さない。"
        Write-GuideItem `
            -Name "Projection Visual passive no HUD" `
            -Target $AituberProjectionVisualUrl `
            -Description "HUD なしの passive 表示。Display Runtime GUI の Stage preview 用。"
        Write-GuideItem `
            -Name "Body map inspector" `
            -Target ("http://{0}:{1}/body-map-inspector?fov=60&scale=1" -f $AituberClientHost, $AituberPort) `
            -Description "Body Schema と各 organ の状態を表示専用で確認する。必要なときだけ開く。"
    }
    if ($EnableThoughtCore) {
        Write-GuideItem `
            -Name "thought-core API" `
            -Target $ThoughtCoreBaseUrl `
            -Description "実験中の思考体 API。画面ではなく /health や /turn を持つローカル HTTP サービス。"
    }
    if (-not $SkipTouchDesignerGui) {
        Write-GuideItem `
            -Name "TD Control GUI/API" `
            -Target ("http://{0}:{1}" -f $TouchDesignerGuiClientHost, $TouchDesignerGuiPort) `
            -Description "スタック状態、TouchDesigner UDP 連携、MediaPipe 状態の確認画面。TouchDesigner 本体ではない。"
    }

    Write-Host ""
    Write-Host "Local APIs and feeds"
    Write-Host "--------------------"
    if (-not $SkipHomeAssistantBridge) {
        Write-GuideItem `
            -Name "Home Assistant bridge health" `
            -Target ("http://{0}:{1}/health" -f $HomeAssistantBridgeClientHost, $HomeAssistantBridgePort) `
            -Description "家電操作ブリッジのヘルスチェック JSON。bind: $HomeAssistantBridgeHost"
    }
    if (-not $SkipEnvironmentState) {
        Write-GuideItem `
            -Name "Environment current state" `
            -Target ("http://{0}:{1}/environment/current" -f $EnvironmentStateClientHost, $EnvironmentStatePort) `
            -Description "現在状態 API。Bearer token が必要。"
        Write-GuideItem `
            -Name "Environment indicators" `
            -Target ("http://{0}:{1}/indicators/current" -f $EnvironmentStateClientHost, $EnvironmentStatePort) `
            -Description "HUD/Cube 背景向けのローカル限定・表示用状態 API。"
    }
    if (-not $SkipMediapipe -and $mediapipeMediaMtxStackLaunched) {
        $encodedMediaUrl = "http%3A%2F%2F127.0.0.1%3A8889%2Fcam0%3Fcontrols%3Dfalse%26muted%3Dtrue%26autoplay%3Dtrue"
        $encodedWsUrl = "ws%3A%2F%2F127.0.0.1%3A$MediapipePort"
        $browserMonitorUrl = "http://127.0.0.1:$MediapipeBrowserMonitorPort/browser_camera_hub_viewer.html?mediaUrl={0}{1}wsUrl={2}{1}target=sword_sign" -f $encodedMediaUrl, ([char]38), $encodedWsUrl
        Write-GuideItem `
            -Name "MediaPipe Browser Monitor" `
            -Target $browserMonitorUrl `
            -Description "MediaMTX の映像と Camera Hub の topic を同時に見る HTTP Browser Monitor。必要なときだけ手動で開く。"
        Write-GuideItem `
            -Name "MediaMTX video" `
            -Target "http://127.0.0.1:8889/cam0?controls=false&muted=true&autoplay=true" `
            -Description "MediaMTX が配信するカメラ映像。映像だけを切り分けたいときに見る。"
        Write-GuideItem `
            -Name "MediaPipe Camera Hub WebSocket" `
            -Target "ws://127.0.0.1:$MediapipePort" `
            -Description "ジェスチャー・カメラ状態の WebSocket。ブラウザで直接開く画面ではない。"
    }
    elseif (-not $SkipMediapipe -and $mediapipeCameraHubLaunched) {
        Write-GuideItem `
            -Name "MediaPipe Camera Hub WebSocket" `
            -Target "ws://127.0.0.1:$MediapipePort" `
            -Description "ジェスチャー・カメラ状態の WebSocket。ブラウザで直接開く画面ではない。"
        Write-GuideItem `
            -Name "MediaPipe Browser Monitor" `
            -Target (Join-Path $MediapipeRoot "apps\browser_camera_hub_viewer.html") `
            -Description "Camera Hub topic の状態確認用。camera-hub モードではMediaMTX映像は起動しないため、映像paneは空になる。"
    }
    elseif (-not $SkipMediapipe) {
        Write-GuideItem `
            -Name "MediaPipe WebSocket" `
            -Target "ws://127.0.0.1:$MediapipePort" `
            -Description "ジェスチャー状態の WebSocket。ブラウザで直接開く画面ではない。"
    }
    if ($LaunchVisionSnapshotProcessor) {
        Write-GuideItem `
            -Name "Vision Snapshot Processor WebSocket" `
            -Target "ws://127.0.0.1:$VisionSnapshotProcessorPort" `
            -Description "snapshot vision state topic の WebSocket。room_light などを配信する。"
    }
    if (-not $SkipVoicevoxCheck -and -not $SkipAituber) {
        Write-GuideItem `
            -Name "VOICEVOX" `
            -Target $VoicevoxUrl `
            -Description "音声合成エンジンの API。画面というより AITuber Kit から使うサービス。"
    }

    Write-Host ""
    Write-Host "Background links"
    Write-Host "----------------"
    if ($EnableThoughtCoreWatch) {
        Write-GuideItem `
            -Name "thought-core watcher" `
            -Target "no browser URL" `
            -Description "ai-talk-core の handoff を thought-core へ渡し、発話イベントを AITuber/TTS へ転送する常駐処理。"
    }
    Write-GuideItem `
        -Name "TouchDesigner UDP receiver" `
        -Target ("{0}:{1}" -f $TouchDesignerUdpClientHost, $TouchDesignerUdpPort) `
        -Description "TouchDesigner 側が受け取る UDP 宛先。このスクリプトは TouchDesigner 本体を起動しない。"

    Write-Host ""
    Write-Host "Commands"
    Write-Host "--------"
    Write-Host "  Status : .\status-home-control-stack.bat"
    Write-Host "  Stop   : Ctrl+C in this terminal, or .\stop-home-control-stack.bat"
    Write-Host ""
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$Label
    )
    $displayCommand = Protect-CameraSelectionCommand -Command (@($FilePath) + $Arguments)
    Write-Host "[$Label] $(Format-CommandLine -Command $displayCommand)"
    if ($DryRun) {
        return
    }
    $process = Start-Process `
        -FilePath $FilePath `
        -ArgumentList $Arguments `
        -WorkingDirectory $WorkingDirectory `
        -NoNewWindow `
        -Wait `
        -PassThru
    if ($process.ExitCode -ne 0) {
        throw "$Label exited with code $($process.ExitCode)"
    }
}

$ProviderEnvironmentInputNames = @(
    "OPENAI_API_KEY", "OPENAI_BASE_URL", "OPENAI_MODEL",
    "THOUGHT_CORE_LLM_API_KEY", "THOUGHT_CORE_LLM_PROVIDER", "THOUGHT_CORE_LLM_ADAPTER",
    "THOUGHT_CORE_LLM_BASE_URL", "THOUGHT_CORE_LLM_MODEL", "THOUGHT_CORE_LLM_TIMEOUT_S", "THOUGHT_CORE_LLM_ENABLED",
    "THOUGHT_CORE_ACTION_LLM_API_KEY", "THOUGHT_CORE_ACTION_LLM_PROVIDER",
    "THOUGHT_CORE_ACTION_LLM_ADAPTER", "THOUGHT_CORE_ACTION_LLM_BASE_URL",
    "THOUGHT_CORE_ACTION_LLM_MODEL", "THOUGHT_CORE_ACTION_LLM_TIMEOUT_S",
    "THOUGHT_CORE_ACTION_LLM_ENABLED"
)
$MinimalBrokerRuntimeEnvironmentNames = @(
    "ComSpec", "SystemRoot", "WINDIR", "PATH", "PATHEXT", "TEMP", "TMP",
    "USERPROFILE", "LOCALAPPDATA", "APPDATA"
)

function New-ServiceSpec {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [hashtable]$Environment = @{},
        [string]$Module = "",
        [string]$Role = "service",
        [string]$StopStrategy = "managed_tree",
        [string[]]$AllowedProcessNames = @(),
        [string]$ChildProcessFile = "",
        [string[]]$RemoveEnvironment = @(),
        [switch]$ClearInheritedEnvironment
    )
    return [pscustomobject]@{
        Name = $Name
        FilePath = $FilePath
        Arguments = $Arguments
        WorkingDirectory = $WorkingDirectory
        Environment = $Environment
        Module = $Module
        Role = $Role
        StopStrategy = $StopStrategy
        AllowedProcessNames = @($AllowedProcessNames)
        ChildProcessFile = $ChildProcessFile
        RemoveEnvironment = @($RemoveEnvironment)
        ClearInheritedEnvironment = [bool]$ClearInheritedEnvironment
    }
}

function Start-SupervisedProcess {
    param([Parameter(Mandatory = $true)][object]$Spec)

    Clear-ChildProcessManifest -Path $Spec.ChildProcessFile

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Spec.FilePath
    $argumentListProperty = $startInfo.GetType().GetProperty("ArgumentList")
    if ($null -ne $argumentListProperty) {
        foreach ($argument in $Spec.Arguments) {
            $startInfo.ArgumentList.Add($argument)
        }
    }
    else {
        $startInfo.Arguments = Format-CommandLine -Command $Spec.Arguments
    }
    $startInfo.WorkingDirectory = $Spec.WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    $startInfo.StandardOutputEncoding = $utf8NoBom
    $startInfo.StandardErrorEncoding = $utf8NoBom
    $startInfo.CreateNoWindow = $true
    if ($Spec.ClearInheritedEnvironment) {
        $minimalRuntimeEnvironment = @{}
        foreach ($name in $MinimalBrokerRuntimeEnvironmentNames) {
            $value = [Environment]::GetEnvironmentVariable($name)
            if (-not [string]::IsNullOrWhiteSpace($value)) {
                $minimalRuntimeEnvironment[$name] = $value
            }
        }
        $startInfo.Environment.Clear()
        foreach ($name in $minimalRuntimeEnvironment.Keys) {
            $startInfo.Environment[$name] = [string]$minimalRuntimeEnvironment[$name]
        }
    }
    foreach ($name in @($Spec.RemoveEnvironment)) {
        if (-not [string]::IsNullOrWhiteSpace([string]$name)) {
            $null = $startInfo.Environment.Remove([string]$name)
        }
    }
    $startInfo.Environment["PYTHONUTF8"] = "1"
    $startInfo.Environment["PYTHONIOENCODING"] = "utf-8"
    $startInfo.Environment["NO_COLOR"] = "1"
    $startInfo.Environment["FORCE_COLOR"] = "0"
    $startInfo.Environment["TERM"] = "dumb"
    if (-not $Spec.ClearInheritedEnvironment) {
        $startInfo.Environment["HOME_CONTROL_WORKSPACE_ROOT"] = $WorkspaceRoot
        $startInfo.Environment["HOME_CONTROL_STACK_STATE_DIR"] = $StateDir
        $startInfo.Environment["MEDIAPIPE_PORT"] = [string]$MediapipePort
        $startInfo.Environment["HOME_ASSISTANT_BRIDGE_HOST"] = $HomeAssistantBridgeClientHost
        $startInfo.Environment["HOME_ASSISTANT_BRIDGE_PORT"] = [string]$HomeAssistantBridgePort
        $startInfo.Environment["ENVIRONMENT_STATE_HOST"] = $EnvironmentStateClientHost
        $startInfo.Environment["ENVIRONMENT_STATE_PORT"] = [string]$EnvironmentStatePort
        $startInfo.Environment["AITUBER_HOST"] = $AituberClientHost
        $startInfo.Environment["AITUBER_PORT"] = [string]$AituberPort
        $startInfo.Environment["AITUBER_URL"] = $AituberProjectionVisualUrl
        $startInfo.Environment["TOUCHDESIGNER_GUI_PORT"] = [string]$TouchDesignerGuiPort
        $startInfo.Environment["TOUCHDESIGNER_UDP_HOST"] = $TouchDesignerUdpClientHost
        $startInfo.Environment["TOUCHDESIGNER_UDP_PORT"] = [string]$TouchDesignerUdpPort
    }
    foreach ($key in $Spec.Environment.Keys) {
        $startInfo.Environment[$key] = [string]$Spec.Environment[$key]
    }
    $null = $startInfo.Environment.Remove("SWORD_FIXED_START_FAILURE_FILE")

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $process.EnableRaisingEvents = $true
    if (-not $process.Start()) {
        throw "failed to start $($Spec.Name)"
    }

    $stdoutEvent = Register-ObjectEvent `
        -InputObject $process `
        -EventName OutputDataReceived `
        -MessageData @{
            Prefix = $Spec.Name
            CameraSelection = [string]$MediapipeCameraName
        } `
        -Action {
            $line = $EventArgs.Data
            if ($null -ne $line) {
                $cleanLine = [regex]::Replace($line, "`e\[[0-?]*[ -/]*[@-~]", "")
                if (-not [string]::IsNullOrWhiteSpace([string]$Event.MessageData.CameraSelection)) {
                    $cleanLine = $cleanLine.Replace(
                        [string]$Event.MessageData.CameraSelection,
                        "<local-camera-selection>"
                    )
                }
                [Console]::Out.WriteLine("[{0}] {1}", $Event.MessageData.Prefix, $cleanLine)
            }
        }
    $stderrEvent = Register-ObjectEvent `
        -InputObject $process `
        -EventName ErrorDataReceived `
        -MessageData @{
            Prefix = $Spec.Name
            CameraSelection = [string]$MediapipeCameraName
        } `
        -Action {
            $line = $EventArgs.Data
            if ($null -ne $line) {
                $cleanLine = [regex]::Replace($line, "`e\[[0-?]*[ -/]*[@-~]", "")
                if (-not [string]::IsNullOrWhiteSpace([string]$Event.MessageData.CameraSelection)) {
                    $cleanLine = $cleanLine.Replace(
                        [string]$Event.MessageData.CameraSelection,
                        "<local-camera-selection>"
                    )
                }
                [Console]::Error.WriteLine("[{0}] {1}", $Event.MessageData.Prefix, $cleanLine)
            }
        }

    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()
    $commandLine = Format-CommandLine -Command (
        Protect-CameraSelectionCommand -Command (@($Spec.FilePath) + $Spec.Arguments)
    )
    Write-Host "[$($Spec.Name)] started PID $($process.Id)"

    return [pscustomobject]@{
        Name = $Spec.Name
        Module = $Spec.Module
        Role = $Spec.Role
        Process = $process
        StdoutEvent = $stdoutEvent
        StderrEvent = $stderrEvent
        WorkingDirectory = $Spec.WorkingDirectory
        CommandLine = $commandLine
        StartedAt = [DateTimeOffset]::Now.ToString("o")
        StopStrategy = $Spec.StopStrategy
        AllowedProcessNames = @($Spec.AllowedProcessNames)
        ChildProcessFile = $Spec.ChildProcessFile
        OwnershipClass = ""
        OwnershipRootPid = 0
        OwnershipRootStartedAt = ""
        OwnershipParentPid = 0
        OwnershipLineage = @()
        OwnershipSeal = ""
        ExpectedModule = ""
        ExpectedPort = 0
        NotifiedExit = $false
    }
}

function Test-VisionSnapshotWorkerCommand {
    param(
        [string]$CommandLine,
        [int]$Port
    )
    if ([string]::IsNullOrWhiteSpace($CommandLine) -or $Port -le 0) {
        return $false
    }
    return (
        $CommandLine -match '(?i)(^|\s)-m\s+vision_snapshot_processor\.main(?:\s|$)' -and
        $CommandLine -match ("(?i)(^|\s)--port\s+{0}(?:\s|$)" -f $Port)
    )
}

function Get-DescendantProcessIds {
    param([Parameter(Mandatory = $true)][int]$RootProcessId)
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    if ($seen.Add($RootProcessId)) {
        $queue.Enqueue($RootProcessId)
    }
    while ($queue.Count -gt 0) {
        $parent = $queue.Dequeue()
        foreach ($child in @($all | Where-Object { [int]$_.ParentProcessId -eq $parent })) {
            $childPid = [int]$child.ProcessId
            if ($seen.Add($childPid)) {
                $queue.Enqueue($childPid)
            }
        }
    }
    return @($seen)
}

function Get-VisionSnapshotOwnershipSeal {
    param(
        [int]$ListenerPid,
        [string]$ListenerStartedAt,
        [int]$ParentPid,
        [int]$RootPid,
        [string]$RootStartedAt,
        [int]$Port,
        [object[]]$Lineage
    )
    $parts = @(
        "vsp_descendant_listener.v0"
        [string]$ListenerPid
        $ListenerStartedAt
        [string]$ParentPid
        [string]$RootPid
        $RootStartedAt
        "vision_snapshot_processor.main"
        [string]$Port
    )
    foreach ($row in @($Lineage)) {
        $parts += (
            "{0}|{1}|{2}|{3}" -f
            [int]$row.pid,
            [int]$row.parent_pid,
            [string]$row.process_name,
            [string]$row.started_at
        )
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = [Text.Encoding]::UTF8.GetBytes(($parts -join "`n"))
        return ([Convert]::ToHexString($sha.ComputeHash($bytes))).ToLowerInvariant()
    }
    finally {
        $sha.Dispose()
    }
}

function Get-ThoughtCoreControllerManifestBinding {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$LaunchNonce,
        [Parameter(Mandatory = $true)][object]$RootChild,
        [Parameter(Mandatory = $true)][int]$Port
    )

    $validationStage = "path"
    if (
        [string]::IsNullOrWhiteSpace($Path) -or
        -not (Test-PathUnderDirectory -Path $Path -Directory $StateDir)
    ) {
        throw "thought_core_controller_manifest_invalid"
    }
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        $validationStage = "shape"
        $item = Get-Item -LiteralPath $Path -ErrorAction Stop
        if ($item.Length -le 0 -or $item.Length -gt 4096) {
            throw "invalid"
        }
        $manifest = Get-Content -Raw -LiteralPath $Path -ErrorAction Stop | ConvertFrom-Json -ErrorAction Stop
        $expectedKeys = @(
            "controller_pid",
            "controller_started_at",
            "expected_module",
            "expected_port",
            "launch_nonce",
            "schema_version",
            "service_class",
            "written_at"
        )
        $actualKeys = @($manifest.PSObject.Properties.Name | Sort-Object)
        if (($actualKeys -join "`n") -cne (($expectedKeys | Sort-Object) -join "`n")) {
            throw "invalid"
        }
        $validationStage = "value"
        if (
            [string]$manifest.schema_version -cne "thought_core_controller.v1" -or
            [string]$manifest.service_class -cne "thought_core_api" -or
            [string]$manifest.launch_nonce -cne $LaunchNonce -or
            [string]$manifest.expected_module -cne "thought_core" -or
            ($manifest.controller_pid -isnot [int] -and $manifest.controller_pid -isnot [long]) -or
            ($manifest.expected_port -isnot [int] -and $manifest.expected_port -isnot [long]) -or
            [int]$manifest.expected_port -ne $Port
        ) {
            throw "invalid"
        }
        $validationStage = "freshness"
        $rootStartedAt = [DateTimeOffset]$RootChild.Process.StartTime
        $controllerStartedAt = [DateTimeOffset]::Parse([string]$manifest.controller_started_at)
        $writtenAt = [DateTimeOffset]::Parse([string]$manifest.written_at)
        $now = [DateTimeOffset]::Now
        if (
            $writtenAt -lt $rootStartedAt.AddSeconds(-2) -or
            $writtenAt -gt $now.AddSeconds(2) -or
            $controllerStartedAt -lt $rootStartedAt.AddSeconds(-2)
        ) {
            throw "invalid"
        }
        $validationStage = "controller_identity"
        $controllerPid = [int]$manifest.controller_pid
        $controller = Get-Process -Id $controllerPid -ErrorAction Stop
        $validationStage = "controller_inspection"
        $controllerIdentity = Get-CimInstance Win32_Process -Filter "ProcessId = $controllerPid" -ErrorAction Stop
        $actualControllerStartedAt = [DateTimeOffset]$controller.StartTime
        $validationStage = "controller_start"
        if ([Math]::Abs(($actualControllerStartedAt - $controllerStartedAt).TotalMilliseconds) -gt 2000) {
            throw "invalid"
        }
        $allowedControllerNames = @("uv")
        if (
            $env:NODE_ENV -eq "test" -and
            -not [string]::IsNullOrWhiteSpace($env:HOME_CONTROL_STACK_TEST_THOUGHT_CORE_PYTHON)
        ) {
            $allowedControllerNames += "python"
        }
        $validationStage = "controller_name"
        if ($allowedControllerNames -notcontains (Normalize-ProcessName -Name ([string]$controller.ProcessName))) {
            throw "invalid"
        }
        $validationStage = "controller_root"
        $rootDescendants = @(Get-DescendantProcessIds -RootProcessId ([int]$RootChild.Process.Id))
        if ($rootDescendants -notcontains $controllerPid) {
            throw "invalid"
        }
        return [pscustomobject]@{
            controller_pid = $controllerPid
            controller_started_at = $controllerStartedAt.ToString("o")
            controller_parent_pid = [int]$controllerIdentity.ParentProcessId
        }
    }
    catch {
        if ($env:NODE_ENV -eq "test") {
            throw "thought_core_controller_manifest_invalid_$validationStage"
        }
        throw "thought_core_controller_manifest_invalid"
    }
}

function Find-SealedDescendantListenerRecord {
    param(
        [Parameter(Mandatory = $true)][object]$RootChild,
        [Parameter(Mandatory = $true)][int]$Port,
        [Parameter(Mandatory = $true)][string]$OwnershipClass,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Module,
        [string]$ControllerManifestPath = "",
        [string]$LaunchNonce = "",
        [int]$TimeoutSeconds = 10
    )
    $classSpec = Get-SwordSealedListenerClassSpec -OwnershipClass $OwnershipClass
    if ($null -eq $classSpec) {
        throw "Sealed descendant listener class is not supported"
    }
    $injectStaleDescendantSnapshotOnce = (
        $env:NODE_ENV -eq "test" -and
        $env:HOME_CONTROL_STACK_TEST_STALE_SEALED_DESCENDANT_SNAPSHOT_ONCE -eq "true"
    )
    $injectStaleDescendantSnapshotAlways = (
        $env:NODE_ENV -eq "test" -and
        $env:HOME_CONTROL_STACK_TEST_STALE_SEALED_DESCENDANT_SNAPSHOT_ALWAYS -eq "true"
    )
    $staleDescendantSnapshotInjected = $false
    $deadline = [DateTimeOffset]::Now.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::Now -lt $deadline) {
        if ($RootChild.Process.HasExited) {
            throw "Sealed descendant listener root exited before ownership was recorded"
        }
        $descendantIds = @(Get-DescendantProcessIds -RootProcessId ([int]$RootChild.Process.Id))
        $connections = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
        $owners = @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
        if ($owners.Count -gt 1) {
            throw "Sealed descendant listener ownership is ambiguous"
        }
        if ($owners.Count -eq 1) {
            $workerPid = [int]$owners[0]
            if (
                (
                    $injectStaleDescendantSnapshotAlways -or
                    ($injectStaleDescendantSnapshotOnce -and -not $staleDescendantSnapshotInjected)
                ) -and
                $descendantIds -contains $workerPid
            ) {
                $descendantIds = @($descendantIds | Where-Object { $_ -ne $workerPid })
                if ($injectStaleDescendantSnapshotOnce) {
                    $staleDescendantSnapshotInjected = $true
                }
            }
            if ($descendantIds -notcontains $workerPid) {
                Start-Sleep -Milliseconds 100
                continue
            }
            $controllerBinding = $null
            if ($OwnershipClass -ceq "thought_core_descendant_listener.v0") {
                $controllerBinding = Get-ThoughtCoreControllerManifestBinding `
                    -Path $ControllerManifestPath `
                    -LaunchNonce $LaunchNonce `
                    -RootChild $RootChild `
                    -Port $Port
                if ($null -eq $controllerBinding) {
                    Start-Sleep -Milliseconds 100
                    continue
                }
            }
            $identity = Get-CimInstance Win32_Process -Filter "ProcessId = $workerPid" -ErrorAction SilentlyContinue
            $worker = Get-Process -Id $workerPid -ErrorAction SilentlyContinue
            if ($null -eq $identity -or $null -eq $worker) {
                throw "Sealed descendant listener identity is unavailable"
            }
            if (
                (Normalize-ProcessName -Name ([string]$worker.ProcessName)) -ne $classSpec.ProcessName -or
                -not (Test-SwordSealedListenerCommand -CommandLine ([string]$identity.CommandLine) -OwnershipClass $OwnershipClass -Port $Port)
            ) {
                throw "Sealed descendant listener identity is invalid"
            }
            $lineage = @()
            $cursor = [int]$identity.ParentProcessId
            for ($depth = 0; $depth -lt 8 -and $cursor -gt 0; $depth++) {
                $ancestorIdentity = Get-CimInstance Win32_Process -Filter "ProcessId = $cursor" -ErrorAction SilentlyContinue
                $ancestor = Get-Process -Id $cursor -ErrorAction SilentlyContinue
                if ($null -eq $ancestorIdentity -or $null -eq $ancestor) {
                    throw "Sealed descendant listener lineage is unavailable"
                }
                $lineage += [pscustomobject]@{
                    pid = $cursor
                    parent_pid = [int]$ancestorIdentity.ParentProcessId
                    process_name = Normalize-ProcessName -Name ([string]$ancestor.ProcessName)
                    started_at = ([DateTimeOffset]$ancestor.StartTime).ToString("o")
                }
                if ($cursor -eq [int]$RootChild.Process.Id) {
                    break
                }
                $cursor = [int]$ancestorIdentity.ParentProcessId
            }
            if ($lineage.Count -eq 0 -or [int]$lineage[-1].pid -ne [int]$RootChild.Process.Id) {
                throw "Sealed descendant listener lineage does not reach the launch root"
            }
            if (
                $null -ne $controllerBinding -and
                @($lineage | Where-Object { [int]$_.pid -eq [int]$controllerBinding.controller_pid }).Count -ne 1
            ) {
                throw "thought_core_controller_manifest_invalid"
            }
            $listenerStartedAt = ([DateTimeOffset]$worker.StartTime).ToString("o")
            $rootStartedAt = [string]$lineage[-1].started_at
            $record = [pscustomobject]@{
                Name = $Name
                Module = $Module
                Role = $classSpec.Role
                Process = $worker
                WorkingDirectory = ""
                CommandLine = ""
                StartedAt = $listenerStartedAt
                StopStrategy = "role_scoped_descendant"
                AllowedProcessNames = @($classSpec.ProcessName)
                ChildProcessFile = ""
                OwnershipClass = $OwnershipClass
                OwnershipRootPid = [int]$RootChild.Process.Id
                OwnershipRootStartedAt = $rootStartedAt
                OwnershipParentPid = [int]$identity.ParentProcessId
                OwnershipLineage = @($lineage)
                OwnershipSeal = ""
                ExpectedModule = $classSpec.ExpectedModule
                ExpectedPort = $Port
                StdoutEvent = $null
                StderrEvent = $null
                NotifiedExit = $false
            }
            $sealEntry = [pscustomobject]@{
                ownership_class = $record.OwnershipClass
                pid = $record.Process.Id
                started_at = $record.StartedAt
                ownership_parent_pid = $record.OwnershipParentPid
                ownership_root_pid = $record.OwnershipRootPid
                ownership_root_started_at = $record.OwnershipRootStartedAt
                expected_module = $record.ExpectedModule
                expected_port = $record.ExpectedPort
                ownership_lineage = @($record.OwnershipLineage)
            }
            $record.OwnershipSeal = Get-SwordSealedListenerOwnershipSeal -Entry $sealEntry
            return $record
        }
        Start-Sleep -Milliseconds 100
    }
    throw "Sealed descendant listener ownership was not observed within the bounded wait"
}

function Stop-SupervisedEvents {
    param([object[]]$Children)
    foreach ($child in $Children) {
        foreach ($subscription in @($child.StdoutEvent, $child.StderrEvent)) {
            if ($null -ne $subscription) {
                Unregister-Event -SubscriptionId $subscription.Id -ErrorAction SilentlyContinue
                Remove-Job -Id $subscription.Id -Force -ErrorAction SilentlyContinue
            }
        }
    }
}

function Stop-RecordedStack {
    if (Test-Path -LiteralPath $StopScript -PathType Leaf) {
        & $StopScript -WorkspaceRoot $WorkspaceRoot -StackStateDir $StateDir -Force
    }
}

function Test-SealedOpenAIBrokerListenerOwnership {
    param([Parameter(Mandatory = $true)][object]$Child)

    if ($Child.Process.HasExited) {
        return $false
    }
    $rootPid = [int]$Child.Process.Id
    $ownedPids = @($rootPid) + @(Get-DescendantProcessIds -RootProcessId $rootPid)
    $owners = @(
        Get-ListeningPortOwner -Port $OpenAIBrokerPort |
            Where-Object { [string]$_.LocalAddress -in @("127.0.0.1", "::1") } |
            Select-Object -ExpandProperty OwningProcess -Unique
    )
    if ($owners.Count -ne 1) {
        return $false
    }
    $listenerPid = [int]$owners[0]
    if ($ownedPids -notcontains $listenerPid) {
        return $false
    }
    $listener = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
    $identity = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid" -ErrorAction SilentlyContinue
    if ($null -eq $listener -or $null -eq $identity) {
        return $false
    }
    $expectedPort = "(?i)(^|\s)--port\s+{0}(?:\s|$)" -f $OpenAIBrokerPort
    return (
        (Normalize-ProcessName -Name ([string]$listener.ProcessName)) -eq "python" -and
        [string]$identity.CommandLine -match "(?i)(^|\s)-m\s+sword_voice_agent\.apps\.openai_broker(?:\s|$)" -and
        [string]$identity.CommandLine -match $expectedPort
    )
}

function Wait-OpenAIBrokerReady {
    param(
        [Parameter(Mandatory = $true)][object]$Child,
        [int]$TimeoutSeconds = 12
    )

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    do {
        if ($Child.Process.HasExited) {
            throw "openai_provider_broker_exited"
        }
        if (-not (Test-SealedOpenAIBrokerListenerOwnership -Child $Child)) {
            Start-Sleep -Milliseconds 100
            continue
        }
        try {
            $response = Invoke-WebRequest `
                -Uri $OpenAIBrokerHealthUrl `
                -Method Get `
                -TimeoutSec 2 `
                -UseBasicParsing `
                -ErrorAction Stop
            if ($response.StatusCode -eq 200) {
                return
            }
        }
        catch {
            # The bounded retry exposes no response body or secret-bearing detail.
        }
        Start-Sleep -Milliseconds 100
    } while ([DateTimeOffset]::UtcNow -lt $deadline)
    throw "openai_provider_broker_unavailable"
}

New-Item -ItemType Directory -Force -Path $LogDir | Out-Null

Assert-Directory -Path $HomeAssistantServerRoot -Label "home-assistant-server"
Assert-Directory -Path $MediapipeRoot -Label "mediapipe-sword-sign"
if ($LaunchVisionSnapshotProcessor) {
    Assert-Directory -Path $VisionSnapshotProcessorRoot -Label "vision-snapshot-processor"
}
Assert-Directory -Path $AituberRoot -Label "aituber-kit"
if (-not $SkipEnvironmentState) {
    Assert-Directory -Path $EnvironmentStateServerRoot -Label "environment-state-server"
}
if ($EnableThoughtCore -or $EnableThoughtCoreWatch) {
    Assert-Directory -Path $ThoughtCoreRoot -Label "control-plane"
}
if ($EnableThoughtCore) {
    if (-not (Test-Path -LiteralPath $ThoughtCoreScript -PathType Leaf)) {
        Write-FixedStartFailureMarker -FailureClass "entrypoint_missing"
        throw "thought-core start script not found: $ThoughtCoreScript"
    }
    if (-not (Test-Path -LiteralPath (Join-Path $ThoughtCoreRoot "services\thought-core") -PathType Container)) {
        Write-FixedStartFailureMarker -FailureClass "dependency_or_tool_missing"
        throw "thought-core service directory not found under: $ThoughtCoreRoot"
    }
    if ($ThoughtCoreLlmProvider -eq "sword-openai-broker") {
        Assert-Directory -Path $BrokerRoot -Label "openai-provider-broker"
        if (-not (Test-Path -LiteralPath (Join-Path $BrokerRoot "pyproject.toml") -PathType Leaf)) {
            Write-FixedStartFailureMarker -FailureClass "dependency_or_tool_missing"
            throw "openai provider broker project not found: $BrokerRoot"
        }
    }
}
if ($EnableThoughtCoreWatch) {
    if (-not (Test-Path -LiteralPath $ThoughtCoreWatchScript -PathType Leaf)) {
        Write-FixedStartFailureMarker -FailureClass "entrypoint_missing"
        throw "thought-core watcher script not found: $ThoughtCoreWatchScript"
    }
    Assert-Directory -Path $AiTalkCoreRoot -Label "ai-talk-core"
}
if (-not $SkipTouchDesignerGui) {
    Assert-Directory -Path $TouchDesignerGuiRoot -Label "touchdesigner-ai-controller"
    Assert-Directory -Path $TouchDesignerGuiToolsRoot -Label "touchdesigner-ai-controller/tools"
}

if (-not $SkipVoicevoxCheck -and -not $SkipAituber -and [string]::IsNullOrWhiteSpace($VoicevoxUrl)) {
    $VoicevoxUrl = Get-DotEnvValue -Path (Join-Path $AituberRoot ".env") -Name "VOICEVOX_SERVER_URL"
    if ([string]::IsNullOrWhiteSpace($VoicevoxUrl)) {
        $VoicevoxUrl = "http://127.0.0.1:50021"
    }
}

$StartThoughtCoreService = $EnableThoughtCore
if ($EnableThoughtCore -and (-not $StopExisting) -and (Test-HttpReachable -Url "$ThoughtCoreBaseUrl/health")) {
    Write-Host "[thought-core] reachable: $ThoughtCoreBaseUrl (using existing service)"
    $StartThoughtCoreService = $false
}

try {
    if (Test-RecordedProcessesAlive) {
        if ($DryRun) {
            Write-Host "[dry-run] a previous home-control stack appears to be running; no stop was attempted."
        }
        elseif ($StopExisting) {
            Stop-RecordedStack
        }
        else {
            $answer = Read-Host "A previous home-control stack appears to be running. Stop it and continue? [y/N]"
            if ($answer -match "^(y|yes)$") {
                Stop-RecordedStack
            }
            else {
                throw "Canceled because a previous home-control stack is still running."
            }
        }
    }
}
catch {
    Write-FixedStartFailureMarker -FailureClass "previous_stack_preflight_failed"
    throw
}

if (-not $DryRun) {
    $requiredPorts = @()
    if ($StartThoughtCoreService -and $ThoughtCoreLlmProvider -eq "sword-openai-broker") {
        $requiredPorts += [pscustomobject]@{
            Label = "openai-provider-broker"
            Port = $OpenAIBrokerPort
        }
    }
    if (-not $SkipHomeAssistantBridge) {
        $requiredPorts += [pscustomobject]@{
            Label = "home-assistant-server"
            Port = $HomeAssistantBridgePort
        }
    }
    if (-not $SkipEnvironmentState) {
        $requiredPorts += [pscustomobject]@{
            Label = "environment-state-server"
            Port = $EnvironmentStatePort
        }
    }
    if (-not $SkipMediapipe) {
        $requiredPorts += [pscustomobject]@{
            Label = "mediapipe-sword-sign"
            Port = $MediapipePort
        }
    }
    if ($LaunchVisionSnapshotProcessor) {
        $requiredPorts += [pscustomobject]@{
            Label = "vision-snapshot-processor"
            Port = $VisionSnapshotProcessorPort
        }
    }
    if (-not $SkipAituber) {
        $requiredPorts += [pscustomobject]@{
            Label = "AITuber Kit"
            Port = $AituberPort
        }
    }
    if (-not $SkipTouchDesignerGui) {
        $requiredPorts += [pscustomobject]@{
            Label = "TouchDesigner control GUI"
            Port = $TouchDesignerGuiPort
        }
    }
    if ($StartThoughtCoreService) {
        $requiredPorts += [pscustomobject]@{
            Label = "thought-core"
            Port = $ThoughtCorePort
        }
    }
    if ($requiredPorts.Count -gt 0) {
        try {
            Resolve-PortConflicts -PortSpecs $requiredPorts
        }
        catch {
            Write-FixedStartFailureMarker -FailureClass "required_port_conflict"
            throw
        }
    }
}

try {
    $uv = Resolve-Tool -Name "uv"
    $npm = Resolve-Tool -Name "npm"
    $node = $null
    if (-not $SkipTouchDesignerGui) {
        $node = Resolve-Tool -Name "node"
    }
    $powerShell = $null
    if ($EnableThoughtCore -or $EnableThoughtCoreWatch) {
        $powerShell = Resolve-CurrentPowerShell
    }
}
catch {
    Write-FixedStartFailureMarker -FailureClass "dependency_or_tool_missing"
    throw
}

if (-not $SkipVoicevoxCheck -and -not $SkipAituber) {
    try {
        Assert-VoicevoxReady -BaseUrl $VoicevoxUrl -TimeoutSeconds $VoicevoxReadyTimeoutSeconds
    }
    catch {
        Write-FixedStartFailureMarker -FailureClass "voicevox_unavailable"
        throw
    }
}

try {
    if (-not $SkipHomeAssistantBridge) {
        Assert-HomeControlBridgeTokenConfigured -EnvPath $HomeAssistantEnvPath
        Report-HomeControlFaultInjectionStatus `
            -EnvPath $HomeAssistantEnvPath `
            -ConfigPath $HomeControlConfigPath `
            -FaultModeOverride:$EnableHomeControlFaultInjection
    }

    if (-not $SkipEnvironmentState) {
        Assert-EnvironmentStateTokenConfigured -EnvPath $HomeAssistantEnvPath
    }
}
catch {
    Write-FixedStartFailureMarker -FailureClass "required_token_missing_or_short"
    throw
}

$EnvironmentVoicevoxUrl = $VoicevoxUrl
if ([string]::IsNullOrWhiteSpace($EnvironmentVoicevoxUrl)) {
    $EnvironmentVoicevoxUrl = "http://127.0.0.1:50021"
}
$EnvironmentVoicevoxHealthUrl = "$($EnvironmentVoicevoxUrl.TrimEnd('/'))/version"
$EnvironmentStateEnvFile = $HomeAssistantEnvPath -replace "\\", "/"

$specs = @()
$mediapipeCameraHubLaunched = $false
$mediapipeMediaMtxStackLaunched = $false
$mediapipeMonitorGuiLaunched = $false
if (-not $SkipHomeAssistantBridge) {
    $homeAssistantBridgeEnvironment = @{
        HOME_CONTROL_CONFIG = $HomeControlConfigPath
    }
    if ($EnableHomeControlFaultInjection) {
        $homeAssistantBridgeEnvironment["HOME_CONTROL_FAULT_MODE"] = "1"
    }
    $specs += New-ServiceSpec `
        -Name "home_assistant_bridge" `
        -FilePath $uv `
        -Arguments @(
            "run",
            "--env-file",
            ".env",
            "python",
            "-m",
            "uvicorn",
            "home_control_bridge.main:app",
            "--host",
            $HomeAssistantBridgeHost,
            "--port",
            [string]$HomeAssistantBridgePort
        ) `
        -WorkingDirectory $HomeAssistantServerRoot `
        -Environment $homeAssistantBridgeEnvironment `
        -Module "home-assistant-server" `
        -Role "api" `
        -AllowedProcessNames @("uv", "uvicorn", "python")
}
if (-not $SkipEnvironmentState) {
    $environmentStateArgs = @(
        "run",
        "--env-file",
        $EnvironmentStateEnvFile,
        "python",
        "-m",
        "environment_state_server.main",
        "--host",
        $EnvironmentStateHost,
        "--port",
        [string]$EnvironmentStatePort,
        "--ha-events-path",
        (Join-Path $HomeAssistantServerRoot ".cache\home_control\events.jsonl"),
        "--state-query-feedback-path",
        $StateQueryFeedbackPath,
        "--camera-hub-url",
        "ws://127.0.0.1:$MediapipePort",
        "--home-assistant-health-url",
        ("http://{0}:{1}/operator" -f $HomeAssistantBridgeClientHost, $HomeAssistantBridgePort),
        "--aituber-url",
        ("http://{0}:{1}" -f $AituberClientHost, $AituberPort),
        "--voicevox-health-url",
        $EnvironmentVoicevoxHealthUrl
    )
    if ($SkipHomeAssistantBridge) {
        $environmentStateArgs += "--disable-ha-events"
    }
    if ($SkipMediapipe) {
        $environmentStateArgs += "--disable-camera-hub"
    }
    if ($LaunchVisionSnapshotProcessor) {
        $environmentStateArgs += @(
            "--vision-topic-url",
            "ws://127.0.0.1:$VisionSnapshotProcessorPort"
        )
    }
    $specs += New-ServiceSpec `
        -Name "environment_state_server" `
        -FilePath $uv `
        -Arguments $environmentStateArgs `
        -WorkingDirectory $EnvironmentStateServerRoot `
        -Module "environment-state-server" `
        -Role "api" `
        -AllowedProcessNames @("uv", "python")
}
$thoughtCoreEnvironment = @{}
foreach ($name in @(
    "THOUGHT_CORE_LLM_ENABLED",
    "THOUGHT_CORE_LLM_PROVIDER",
    "THOUGHT_CORE_LLM_ADAPTER",
    "THOUGHT_CORE_ACTION_LLM_ENABLED",
    "THOUGHT_CORE_LLM_BASE_URL",
    "THOUGHT_CORE_LLM_MODEL",
    "THOUGHT_CORE_LLM_TIMEOUT_S",
    "THOUGHT_CORE_LLM_MAX_CHARS",
    "THOUGHT_CORE_CODEX_CLI_PATH",
    "THOUGHT_CORE_CODEX_CLI_CWD",
    "THOUGHT_CORE_CODEX_CLI_WORKSPACE_ROOT",
    "THOUGHT_CORE_CODEX_CLI_PROJECT_ROOT",
    "THOUGHT_CORE_CODEX_CLI_MODEL",
    "THOUGHT_CORE_CODEX_CLI_REASONING_EFFORT",
    "THOUGHT_CORE_CODEX_CLI_MODEL_REASONING_EFFORT",
    "THOUGHT_CORE_CODEX_CLI_VERBOSITY",
    "THOUGHT_CORE_CODEX_CLI_CONFIG_OVERRIDES",
    "THOUGHT_CORE_CODEX_CLI_EXPECTED_VERSION",
    "THOUGHT_CORE_CODEX_CLI_VERSION_POLICY",
    "THOUGHT_CORE_CODEX_CLI_VERSION_TIMEOUT_S",
    "THOUGHT_CORE_CODEX_CLI_PROFILE",
    "THOUGHT_CORE_CODEX_CLI_TIMEOUT_S",
    "THOUGHT_CORE_CODEX_CLI_MAX_CHARS",
    "THOUGHT_CORE_CODEX_CLI_MODE",
    "THOUGHT_CORE_CODEX_CLI_SANDBOX",
    "THOUGHT_CORE_CODEX_CLI_APPROVAL",
    "THOUGHT_CORE_CODEX_CLI_EPHEMERAL",
    "CODEX_CLI_PATH",
    "THOUGHT_CORE_PERSONA",
    "SWORD_THOUGHT_CORE_PERSONA",
    "THOUGHT_CORE_HOME_HTTP_TIMEOUT_S",
    "THOUGHT_CORE_ROOM_LIGHT_WAIT_TIMEOUT_MS",
    "THOUGHT_CORE_TOOLS_ADAPTER",
    "OPENAI_BASE_URL",
    "OPENAI_MODEL"
)) {
    $value = [Environment]::GetEnvironmentVariable($name)
    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = Get-DotEnvValue -Path $ThoughtCoreEnvPath -Name $name
    }
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        $thoughtCoreEnvironment[$name] = $value
    }
}
if (-not $thoughtCoreEnvironment.ContainsKey("THOUGHT_CORE_PERSONA")) {
    $thoughtCoreEnvironment["THOUGHT_CORE_PERSONA"] = "cheerful_ossan"
}
if ((-not $ThoughtCoreNoProvider) -and $ThoughtCoreLlmProvider -ne "configured") {
    $thoughtCoreRuntimeProvider = if ($ThoughtCoreLlmProvider -in @("codex-cli", "codex-cli-luna")) {
        "codex-cli"
    } else {
        $ThoughtCoreLlmProvider
    }
    $thoughtCoreEnvironment["THOUGHT_CORE_LLM_ENABLED"] = "1"
    $thoughtCoreEnvironment["THOUGHT_CORE_FORCE_NO_PROVIDER"] = ""
    $thoughtCoreEnvironment["THOUGHT_CORE_LLM_PROVIDER"] = $thoughtCoreRuntimeProvider
    if ($thoughtCoreRuntimeProvider -eq "sword-openai-broker") {
        foreach ($name in $ProviderEnvironmentInputNames) {
            $thoughtCoreEnvironment.Remove($name)
        }
        $thoughtCoreEnvironment["THOUGHT_CORE_LLM_ENABLED"] = "1"
        $thoughtCoreEnvironment["THOUGHT_CORE_LLM_PROVIDER"] = "sword-openai-broker"
        $thoughtCoreEnvironment["THOUGHT_CORE_LLM_BASE_URL"] = $OpenAIBrokerBaseUrl
        $thoughtCoreEnvironment["THOUGHT_CORE_LLM_MODEL"] = "gpt-4o-mini"
        $thoughtCoreEnvironment["THOUGHT_CORE_LLM_TIMEOUT_S"] = "12"
        $thoughtCoreEnvironment["THOUGHT_CORE_ACTION_LLM_ENABLED"] = "0"
    }
    else {
        $thoughtCoreEnvironment["THOUGHT_CORE_LLM_ADAPTER"] = $thoughtCoreRuntimeProvider
    }
    if ($thoughtCoreRuntimeProvider -eq "codex-cli") {
        # Compatibility preset only: this response-only route does not satisfy the
        # agentic intent/product boundary in ADR 0003. It is retained while the
        # unified capability-proposal route is implemented.
        $thoughtCoreEnvironment["THOUGHT_CORE_ACTION_LLM_ENABLED"] = "0"
        # Codex may vary only visible wording in this compatibility route. Do not
        # treat its deterministic action result as product or filming acceptance.
        $thoughtCoreEnvironment["THOUGHT_CORE_LLM_VISIBLE_SPEECH_ENABLED"] = "1"
        $thoughtCoreEnvironment["THOUGHT_CORE_REQUIRE_LLM_VISIBLE_SPEECH"] = "0"
        if ($ThoughtCoreLlmProvider -eq "codex-cli-luna") {
            $thoughtCoreEnvironment["THOUGHT_CORE_CODEX_CLI_MODEL"] = "gpt-5.6-luna"
            $thoughtCoreEnvironment["THOUGHT_CORE_CODEX_CLI_REASONING_EFFORT"] = "low"
        } else {
            $thoughtCoreEnvironment["THOUGHT_CORE_CODEX_CLI_MODEL"] = "gpt-5.6-terra"
            $thoughtCoreEnvironment["THOUGHT_CORE_CODEX_CLI_REASONING_EFFORT"] = "medium"
        }
        $thoughtCoreEnvironment["THOUGHT_CORE_CODEX_CLI_MODE"] = "respond"
        $thoughtCoreEnvironment["THOUGHT_CORE_CODEX_CLI_SANDBOX"] = "read-only"
        $thoughtCoreEnvironment["THOUGHT_CORE_CODEX_CLI_APPROVAL"] = "never"
        $thoughtCoreEnvironment["THOUGHT_CORE_CODEX_CLI_EPHEMERAL"] = "true"
    }
}
if ($ThoughtCoreNoProvider) {
    $thoughtCoreEnvironment["THOUGHT_CORE_FORCE_NO_PROVIDER"] = "1"
    $thoughtCoreEnvironment["THOUGHT_CORE_LLM_ENABLED"] = "0"
    $thoughtCoreEnvironment["THOUGHT_CORE_ACTION_LLM_ENABLED"] = "0"
    foreach ($name in @(
        "THOUGHT_CORE_LLM_PROVIDER",
        "THOUGHT_CORE_LLM_ADAPTER",
        "THOUGHT_CORE_LLM_BASE_URL",
        "THOUGHT_CORE_LLM_API_KEY",
        "THOUGHT_CORE_LLM_MODEL",
        "THOUGHT_CORE_CODEX_CLI_PATH",
        "THOUGHT_CORE_CODEX_CLI_CWD",
        "THOUGHT_CORE_CODEX_CLI_WORKSPACE_ROOT",
        "THOUGHT_CORE_CODEX_CLI_PROJECT_ROOT",
        "THOUGHT_CORE_CODEX_CLI_MODEL",
        "THOUGHT_CORE_CODEX_CLI_REASONING_EFFORT",
        "THOUGHT_CORE_CODEX_CLI_MODEL_REASONING_EFFORT",
        "THOUGHT_CORE_CODEX_CLI_VERBOSITY",
        "THOUGHT_CORE_CODEX_CLI_CONFIG_OVERRIDES",
        "THOUGHT_CORE_CODEX_CLI_EXPECTED_VERSION",
        "THOUGHT_CORE_CODEX_CLI_VERSION_POLICY",
        "THOUGHT_CORE_CODEX_CLI_VERSION_TIMEOUT_S",
        "THOUGHT_CORE_CODEX_CLI_PROFILE",
        "THOUGHT_CORE_CODEX_CLI_TIMEOUT_S",
        "THOUGHT_CORE_CODEX_CLI_MAX_CHARS",
        "THOUGHT_CORE_CODEX_CLI_MODE",
        "THOUGHT_CORE_CODEX_CLI_SANDBOX",
        "THOUGHT_CORE_CODEX_CLI_APPROVAL",
        "THOUGHT_CORE_CODEX_CLI_EPHEMERAL",
        "CODEX_CLI_PATH",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL"
    )) {
        $thoughtCoreEnvironment[$name] = ""
    }
}
if ($EnableThoughtCore -and (-not $SkipHomeAssistantBridge)) {
    $homeControlToken = [Environment]::GetEnvironmentVariable("HOME_CONTROL_API_TOKEN")
    if ([string]::IsNullOrWhiteSpace($homeControlToken)) {
        $homeControlToken = Get-DotEnvValue -Path $HomeAssistantEnvPath -Name "HOME_CONTROL_API_TOKEN"
    }
    if (-not [string]::IsNullOrWhiteSpace($homeControlToken)) {
        $thoughtCoreEnvironment["THOUGHT_CORE_TOOLS_ADAPTER"] = "home_control"
        $thoughtCoreEnvironment["HOME_CONTROL_BRIDGE_URL"] = "http://{0}:{1}" -f $HomeAssistantBridgeClientHost, $HomeAssistantBridgePort
        $thoughtCoreEnvironment["HOME_ASSISTANT_BRIDGE_URL"] = "http://{0}:{1}" -f $HomeAssistantBridgeClientHost, $HomeAssistantBridgePort
        $thoughtCoreEnvironment["HOME_CONTROL_API_TOKEN"] = $homeControlToken
    }
}
if ($EnableThoughtCore -and (-not $SkipEnvironmentState)) {
    $environmentToken = [Environment]::GetEnvironmentVariable("ENVIRONMENT_API_TOKEN")
    if ([string]::IsNullOrWhiteSpace($environmentToken)) {
        $environmentToken = Get-DotEnvValue -Path $HomeAssistantEnvPath -Name "ENVIRONMENT_API_TOKEN"
    }
    if ([string]::IsNullOrWhiteSpace($environmentToken)) {
        $environmentToken = [Environment]::GetEnvironmentVariable("HOME_CONTROL_API_TOKEN")
    }
    if ([string]::IsNullOrWhiteSpace($environmentToken)) {
        $environmentToken = Get-DotEnvValue -Path $HomeAssistantEnvPath -Name "HOME_CONTROL_API_TOKEN"
    }
    if (-not [string]::IsNullOrWhiteSpace($environmentToken)) {
        $thoughtCoreEnvironment["ENVIRONMENT_STATE_URL"] = "http://{0}:{1}/environment/current" -f $EnvironmentStateClientHost, $EnvironmentStatePort
        $thoughtCoreEnvironment["ENVIRONMENT_API_TOKEN"] = $environmentToken
    }
}
$displayRuntimeEnvironment = @{}
if ($thoughtCoreEnvironment.ContainsKey("THOUGHT_CORE_TOOLS_ADAPTER")) {
    $displayRuntimeEnvironment["THOUGHT_CORE_TOOLS_ADAPTER"] = $thoughtCoreEnvironment["THOUGHT_CORE_TOOLS_ADAPTER"]
}
$thoughtCoreLaunchNonce = ""
if ($StartThoughtCoreService) {
    Clear-ChildProcessManifest -Path $ThoughtCoreControllerManifestPath
    $thoughtCoreLaunchNonce = [Guid]::NewGuid().ToString("N")
    $thoughtCoreEnvironment["SWORD_THOUGHT_CORE_CONTROLLER_MANIFEST"] = $ThoughtCoreControllerManifestPath
    $thoughtCoreEnvironment["SWORD_THOUGHT_CORE_LAUNCH_NONCE"] = $thoughtCoreLaunchNonce
}
if ($StartThoughtCoreService -and $ThoughtCoreLlmProvider -eq "sword-openai-broker") {
    $specs += New-ServiceSpec `
        -Name "openai_provider_broker" `
        -FilePath $uv `
        -Arguments @("run", "python", "-m", "sword_voice_agent.apps.openai_broker", "--port", [string]$OpenAIBrokerPort) `
        -WorkingDirectory $BrokerRoot `
        -Module "sword-voice-agent" `
        -Role "openai_provider_broker" `
        -AllowedProcessNames @("uv", "python") `
        -RemoveEnvironment $ProviderEnvironmentInputNames `
        -ClearInheritedEnvironment
}
if ($StartThoughtCoreService) {
    $specs += New-ServiceSpec `
        -Name "thought_core_api" `
        -FilePath $powerShell `
        -Arguments @(
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            $ThoughtCoreScript,
            "-HostName",
            $ThoughtCoreHost,
            "-Port",
            [string]$ThoughtCorePort,
            "-StatusDir",
            $ThoughtCoreStatusDir,
            "-SkipEnvImport"
        ) `
        -WorkingDirectory $ThoughtCoreRoot `
        -Environment $thoughtCoreEnvironment `
        -Module "control-plane-core" `
        -Role "thought_core_api" `
        -AllowedProcessNames @("pwsh", "powershell", "uv", "python") `
        -RemoveEnvironment $ProviderEnvironmentInputNames
}
if (-not $SkipMediapipe) {
    $cameraHubServerPath = Join-Path $MediapipeRoot "apps\serve_camera_hub.py"
    $cameraHubGuiPath = Join-Path $MediapipeRoot "apps\camera_hub_gui.py"
    $cameraHubStackPath = Join-Path $MediapipeRoot "scripts\camera_hub_stack.py"

    if ($MediapipeMode -eq "mediamtx") {
        if (-not (Test-Path -LiteralPath $cameraHubStackPath -PathType Leaf)) {
            throw "MediaPipe MediaMTX stack entrypoint not found: scripts\camera_hub_stack.py"
        }

        $cameraHubStackArgs = @(
            "run",
            "python",
            "scripts\camera_hub_stack.py",
            "--camera-name",
            $MediapipeCameraName,
            "--width",
            [string]$MediapipeCameraWidth,
            "--height",
            [string]$MediapipeCameraHeight,
            "--fps",
            [string]$MediapipeCameraFps,
            "--ffmpeg-video-source",
            $MediapipeVideoSource,
            "--ffmpeg-input-codec",
            $MediapipeCameraInputCodec,
            "--hub-port",
            [string]$MediapipePort,
            "--viewer-port",
            [string]$MediapipeBrowserMonitorPort
        )
        if ($StopExisting) {
            $cameraHubStackArgs += "--force-stop-existing"
        }
        if ($MediapipeNoBrowser -or -not $MediapipeOpenBrowser) {
            $cameraHubStackArgs += "--no-browser"
        }
        if ($MediapipePythonGui) {
            $cameraHubStackArgs += "--python-gui"
            $mediapipeMonitorGuiLaunched = $true
        }

        $specs += New-ServiceSpec `
            -Name "mediapipe_camera_hub_stack" `
            -FilePath $uv `
            -Arguments $cameraHubStackArgs `
            -WorkingDirectory $MediapipeRoot `
            -Module "mediapipe-sword-sign" `
            -Role "camera_hub_stack" `
            -AllowedProcessNames @("uv", "python", "mediamtx", "ffmpeg") `
            -ChildProcessFile $MediapipeCameraHubChildProcessFile
        $mediapipeCameraHubLaunched = $true
        $mediapipeMediaMtxStackLaunched = $true
    }
    elseif ($MediapipeMode -eq "camera-hub" -or $MediapipeMode -eq "gui") {
        if (-not (Test-Path -LiteralPath $cameraHubServerPath -PathType Leaf)) {
            throw "MediaPipe Camera Hub entrypoint not found: apps\serve_camera_hub.py"
        }

        $specs += New-ServiceSpec `
            -Name "mediapipe_camera_hub" `
            -FilePath $uv `
            -Arguments @(
                "run",
                "python",
                "apps\serve_camera_hub.py",
                "--host",
                "127.0.0.1",
                "--port",
                [string]$MediapipePort,
                "--gesture-every",
                "0.1",
                "--gesture-model-complexity",
                "0",
                "--publish-landmarks"
            ) `
            -WorkingDirectory $MediapipeRoot `
            -Module "mediapipe-sword-sign" `
            -Role "camera_hub" `
            -AllowedProcessNames @("uv", "python")
        $mediapipeCameraHubLaunched = $true

        if ($MediapipeMode -eq "gui" -and (Test-Path -LiteralPath $cameraHubGuiPath -PathType Leaf)) {
            $specs += New-ServiceSpec `
                -Name "mediapipe_camera_hub_gui" `
                -FilePath $uv `
                -Arguments @(
                    "run",
                    "python",
                    "apps\camera_hub_gui.py"
                ) `
                -WorkingDirectory $MediapipeRoot `
                -Module "mediapipe-sword-sign" `
                -Role "camera_hub_gui" `
                -AllowedProcessNames @("uv", "python")
            $mediapipeMonitorGuiLaunched = $true
        }
    }
}
if ($LaunchVisionSnapshotProcessor) {
    $visionSnapshotEntrypoint = Join-Path $VisionSnapshotProcessorRoot "src\vision_snapshot_processor\main.py"
    if (-not (Test-Path -LiteralPath $visionSnapshotEntrypoint -PathType Leaf)) {
        throw "Vision Snapshot Processor entrypoint not found: src\vision_snapshot_processor\main.py"
    }

    $specs += New-ServiceSpec `
        -Name "vision_snapshot_processor" `
        -FilePath $uv `
        -Arguments @(
            "run",
            "python",
            "-m",
            "vision_snapshot_processor.main",
            "--host",
            "127.0.0.1",
            "--port",
            [string]$VisionSnapshotProcessorPort,
            "--camera-source",
            "rtsp://127.0.0.1:8554/cam0",
            "--frame-id",
            "cam0",
            "--processor",
            "room_light"
        ) `
        -WorkingDirectory $VisionSnapshotProcessorRoot `
        -Module "vision-snapshot-processor" `
        -Role "vision_snapshot_processor" `
        -AllowedProcessNames @("uv", "python")
}
if (-not $SkipAituber) {
    $projectionVisualAIService = [Environment]::GetEnvironmentVariable("NEXT_PUBLIC_PROJECTION_VISUAL_AI_SERVICE", "Process")
    if ([string]::IsNullOrWhiteSpace($projectionVisualAIService)) {
        $projectionVisualAIService = "thought-core"
    }
    $aituberAIService = [Environment]::GetEnvironmentVariable("NEXT_PUBLIC_SELECT_AI_SERVICE", "Process")
    if ([string]::IsNullOrWhiteSpace($aituberAIService)) {
        if ($EnableThoughtCore) {
            $aituberAIService = "thought-core"
        }
        else {
            $aituberAIService = $projectionVisualAIService
        }
    }
    $gestureVoiceBridgeEnabled = if ($SkipMediapipe) { "false" } else { "true" }
    $aituberEnvironment = @{
        THOUGHT_CORE_BASE_URL = $ThoughtCoreBaseUrl
        NEXT_PUBLIC_THOUGHT_CORE_BASE_URL = $ThoughtCoreBaseUrl
        NEXT_PUBLIC_THOUGHT_CORE_SESSION_ID = "aituber-kit"
        NEXT_PUBLIC_SYSTEM_CELL_AI_SERVICE = $aituberAIService
        NEXT_PUBLIC_SELECT_AI_SERVICE = $aituberAIService
        NEXT_PUBLIC_PROJECTION_VISUAL_AI_SERVICE = $projectionVisualAIService
        NEXT_PUBLIC_DISPLAY_RUNTIME_STATUS_URL = "http://{0}:{1}/api/status" -f $TouchDesignerGuiClientHost, $TouchDesignerGuiPort
        NEXT_PUBLIC_TD_CONTROL_GUI_STATUS_URL = "http://{0}:{1}/api/status" -f $TouchDesignerGuiClientHost, $TouchDesignerGuiPort
        NEXT_PUBLIC_ENVIRONMENT_INDICATORS_URL = "http://{0}:{1}/indicators/current" -f $EnvironmentStateClientHost, $EnvironmentStatePort
        NEXT_PUBLIC_REFLEX_GESTURE_WS_URL = "ws://127.0.0.1:$MediapipePort"
        NEXT_PUBLIC_GESTURE_VOICE_WS_URL = "ws://127.0.0.1:$MediapipePort"
        NEXT_PUBLIC_GESTURE_VOICE_BRIDGE_ENABLED = $gestureVoiceBridgeEnabled
    }
    $specs += New-ServiceSpec `
        -Name "aituber_kit" `
        -FilePath $npm `
        -Arguments @(
            "run",
            "dev",
            "--",
            "--hostname",
            $AituberHost,
            "--port",
            [string]$AituberPort
        ) `
        -WorkingDirectory $AituberRoot `
        -Environment $aituberEnvironment `
        -Module "aituber-kit" `
        -Role "frontend" `
        -AllowedProcessNames @("cmd", "node", "npm")
}
if ($EnableThoughtCoreWatch) {
    $thoughtCoreWatchArgs = @(
        "-NoLogo",
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $ThoughtCoreWatchScript,
        "-EnvPath",
        $ThoughtCoreEnvPath,
        "-AiTalkCoreRoot",
        $AiTalkCoreRoot,
        "-ThoughtCoreBaseUrl",
        $ThoughtCoreBaseUrl,
        "-StatusDir",
        $ThoughtCoreWatchStatusDir
    )
    if (-not $SkipAituber) {
        $thoughtCoreWatchArgs += @(
            "-AituberPort",
            [string]$AituberPort,
            "-AituberMessageUrl",
            ("http://{0}:{1}/api/messages/?clientId=thought-core&type=direct_send" -f $AituberClientHost, $AituberPort)
        )
    }
    if (-not [string]::IsNullOrWhiteSpace($ThoughtCoreWatchAituberHttpTimeout)) {
        $thoughtCoreWatchArgs += @("-AituberHttpTimeout", $ThoughtCoreWatchAituberHttpTimeout)
    }

    $specs += New-ServiceSpec `
        -Name "thought_core_watcher" `
        -FilePath $powerShell `
        -Arguments $thoughtCoreWatchArgs `
        -WorkingDirectory $ThoughtCoreRoot `
        -Module "control-plane-core" `
        -Role "thought_core_watcher" `
        -AllowedProcessNames @("pwsh", "powershell", "uv", "python")
}
if (-not $SkipTouchDesignerGui) {
    $specs += New-ServiceSpec `
        -Name "touchdesigner_control_gui" `
        -FilePath $node `
        -Arguments @(
            "server.js",
            "--workspace",
            $WorkspaceRoot,
            "--port",
            [string]$TouchDesignerGuiPort,
            "--host",
            $TouchDesignerGuiHost,
            "--home-assistant-bridge-host",
            $HomeAssistantBridgeClientHost,
            "--home-assistant-bridge-port",
            [string]$HomeAssistantBridgePort,
            "--environment-state-host",
            $EnvironmentStateClientHost,
            "--environment-state-port",
            [string]$EnvironmentStatePort,
            "--aituber-host",
            $AituberClientHost,
            "--aituber-port",
            [string]$AituberPort,
            "--aituber-url",
            $AituberProjectionVisualUrl,
            "--touchdesigner-host",
            $TouchDesignerUdpClientHost,
            "--touchdesigner-port",
            [string]$TouchDesignerUdpPort,
            "--thought-core-host",
            $ThoughtCoreClientHost,
            "--thought-core-port",
            [string]$ThoughtCorePort
        ) `
        -WorkingDirectory $TouchDesignerGuiToolsRoot `
        -Environment $displayRuntimeEnvironment `
        -Module "touchdesigner-ai-controller" `
        -Role "display_runtime" `
        -AllowedProcessNames @("node")
}

if ($DryRun) {
    foreach ($spec in $specs) {
        $displayCommand = Protect-CameraSelectionCommand -Command (@($spec.FilePath) + $spec.Arguments)
        Write-Host "[$($spec.Name)] $(Format-CommandLine -Command $displayCommand)"
    }
    return
}

Assert-SelectedServiceEntrypoints -Specs $specs

$children = @()
$shutdownStarted = $false
$exitCode = 0

try {
    $mediapipeCameraHubChild = $null
    $delayedVisionSnapshotSpecs = @()
    foreach ($spec in $specs) {
        if ($spec.Name -eq "vision_snapshot_processor") {
            $delayedVisionSnapshotSpecs += $spec
            continue
        }
        try {
            $rootChild = Start-SupervisedProcess -Spec $spec
        }
        catch {
            if ($children.Count -eq 0) {
                Write-FixedStartFailureMarker -FailureClass "first_service_spawn_failed"
            }
            throw
        }
        $children += $rootChild
        Save-StartupPidState -Children $children
        if ($spec.Name -eq "openai_provider_broker") {
            Wait-OpenAIBrokerReady -Child $rootChild -TimeoutSeconds 12
        }
        $sealedListener = switch ($spec.Name) {
            "home_assistant_bridge" {
                Find-SealedDescendantListenerRecord `
                    -RootChild $rootChild `
                    -Port $HomeAssistantBridgePort `
                    -OwnershipClass "home_control_bridge_descendant_listener.v0" `
                    -Name "home_assistant_bridge_listener" `
                    -Module "home-assistant-server"
                break
            }
            "thought_core_api" {
                Find-SealedDescendantListenerRecord `
                    -RootChild $rootChild `
                    -Port $ThoughtCorePort `
                    -OwnershipClass "thought_core_descendant_listener.v0" `
                    -Name "thought_core_api_listener" `
                    -Module "control-plane-core" `
                    -ControllerManifestPath $ThoughtCoreControllerManifestPath `
                    -LaunchNonce $thoughtCoreLaunchNonce
                break
            }
            default { $null }
        }
        if ($null -ne $sealedListener) {
            $children += $sealedListener
            Save-StartupPidState -Children $children
        }
        if ($spec.Name -eq "mediapipe_camera_hub_stack") {
            $mediapipeCameraHubChild = $rootChild
        }
        Start-Sleep -Milliseconds 500
    }
    if ($null -ne $mediapipeCameraHubChild) {
        Wait-CameraHubStackReady -Child $mediapipeCameraHubChild -TimeoutSeconds $MediapipeReadyTimeoutSeconds
    }
    foreach ($spec in $delayedVisionSnapshotSpecs) {
        $visionSnapshotRootChild = Start-SupervisedProcess -Spec $spec
        $children += $visionSnapshotRootChild
        Save-StartupPidState -Children $children
        $visionSnapshotListener = Find-SealedDescendantListenerRecord `
            -RootChild $visionSnapshotRootChild `
            -Port $VisionSnapshotProcessorPort `
            -OwnershipClass "vsp_descendant_listener.v0" `
            -Name "vision_snapshot_processor_listener" `
            -Module "vision-snapshot-processor"
        $children += $visionSnapshotListener
        Save-StartupPidState -Children $children
    }

    Write-StackEndpointGuide

    while ($true) {
        $running = 0
        foreach ($child in $children) {
            if ($child.Process.HasExited) {
                if (-not $child.NotifiedExit) {
                    $child.NotifiedExit = $true
                    $code = $child.Process.ExitCode
                    Write-Host "[$($child.Name)] exited code=$code"
                    if ($code -ne 0 -and -not $shutdownStarted) {
                        $exitCode = $code
                        $shutdownStarted = $true
                        Stop-RecordedStack
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
    Write-Host "Ctrl+C received; stopping stack..."
}
catch {
    Write-FixedStartFailureMarker -FailureClass "stack_start_failed_unknown"
    [Console]::Error.WriteLine("home_control_stack_start_failed")
    $exitCode = 1
}
finally {
    if (-not $shutdownStarted) {
        $liveChildren = @($children | Where-Object { -not $_.Process.HasExited })
        if ($liveChildren.Count -gt 0 -or (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
            $shutdownStarted = $true
            Stop-RecordedStack
        }
    }
    Stop-SupervisedEvents -Children $children
}

exit $exitCode
