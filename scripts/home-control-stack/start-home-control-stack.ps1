param(
    [string]$WorkspaceRoot = "",
    [string]$HomeAssistantServerRoot = "",
    [string]$MediapipeRoot = "",
    [string]$VisionSnapshotProcessorRoot = "",
    [string]$AituberRoot = "",
    [string]$TouchDesignerGuiRoot = "",
    [string]$DifyWatchRoot = "",
    [string]$EnvironmentStateServerRoot = "",
    [string]$DifyDockerRoot = "C:\Users\kawai\works\dify\docker",
    [int]$HomeAssistantBridgePort = 8787,
    [string]$HomeAssistantBridgeHost = "127.0.0.1",
    [string]$HomeControlConfigPath = "",
    [int]$EnvironmentStatePort = 8790,
    [int]$MediapipePort = 8765,
    [int]$VisionSnapshotProcessorPort = 8776,
    [int]$AituberPort = 3000,
    [string]$AituberHost = "127.0.0.1",
    [int]$TouchDesignerGuiPort = 8788,
    [string]$TouchDesignerGuiHost = "127.0.0.1",
    [int]$DifyPort = 8080,
    [string]$VoicevoxUrl = "",
    [ValidateSet("gui", "headless", "camera-hub", "mediamtx")]
    [string]$MediapipeMode = "mediamtx",
    [string]$MediapipeCameraName = "HD Pro Webcam C920",
    [switch]$MediapipeOpenBrowser,
    [switch]$MediapipeNoBrowser,
    [switch]$MediapipePythonGui,
    [switch]$SkipDify,
    [switch]$SkipVoicevoxCheck,
    [switch]$SkipHomeAssistantBridge,
    [switch]$SkipEnvironmentState,
    [switch]$SkipMediapipe,
    [switch]$SkipVisionSnapshotProcessor,
    [switch]$SkipAituber,
    [switch]$SkipDifyWatch,
    [switch]$SkipTouchDesignerGui,
    [switch]$StopExisting,
    [switch]$EnableHomeControlFaultInjection,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
[Console]::InputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

. (Join-Path $PSScriptRoot "resolve-home-control-workspace.ps1")
$WorkspaceRoot = Resolve-HomeControlWorkspaceRoot -WorkspaceRoot $WorkspaceRoot -ScriptRoot $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($HomeAssistantServerRoot)) {
    $HomeAssistantServerRoot = Join-Path $WorkspaceRoot "home-assistant-server"
}
if ([string]::IsNullOrWhiteSpace($MediapipeRoot)) {
    $MediapipeRoot = Join-Path $WorkspaceRoot "mediapipe-sword-sign"
}
if ([string]::IsNullOrWhiteSpace($VisionSnapshotProcessorRoot)) {
    $VisionSnapshotProcessorRoot = Join-Path $WorkspaceRoot "vision-snapshot-processor"
}
if ([string]::IsNullOrWhiteSpace($AituberRoot)) {
    $AituberRoot = Join-Path $WorkspaceRoot "aituber-kit"
}
if ([string]::IsNullOrWhiteSpace($TouchDesignerGuiRoot)) {
    $TouchDesignerGuiRoot = Join-Path $WorkspaceRoot "touchdesigner-ai-controller"
}
if ([string]::IsNullOrWhiteSpace($DifyWatchRoot)) {
    $DifyWatchRoot = Join-Path $WorkspaceRoot "sword-voice-agent"
}
if ([string]::IsNullOrWhiteSpace($EnvironmentStateServerRoot)) {
    $EnvironmentStateServerRoot = Join-Path $WorkspaceRoot "environment-state-server"
}
if ([string]::IsNullOrWhiteSpace($HomeControlConfigPath)) {
    $HomeControlConfigPath = Join-Path $HomeAssistantServerRoot "config\home-control.yaml"
}
$HomeControlConfigPath = (Resolve-Path -LiteralPath $HomeControlConfigPath).Path
$HomeAssistantEnvPath = Join-Path $HomeAssistantServerRoot ".env"
$TouchDesignerGuiToolsRoot = Join-Path $TouchDesignerGuiRoot "tools"
$DifyWatchScript = Join-Path $DifyWatchRoot "scripts\start-dify-watch.ps1"
$DifyWatchEnvPath = Join-Path $DifyWatchRoot ".env"
$LaunchVisionSnapshotProcessor = ((-not $SkipVisionSnapshotProcessor) -and (-not $SkipMediapipe) -and ($MediapipeMode -eq "mediamtx"))

$StateDir = Join-Path $WorkspaceRoot ".cache\home-control-stack"
$LogDir = Join-Path $StateDir "logs"
$PidFile = Join-Path $StateDir "pids.json"
$StopScript = Join-Path $PSScriptRoot "stop-home-control-stack.ps1"
$DifyWatchStatusDir = Join-Path $StateDir "dify-watcher"
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

function Test-DockerDaemon {
    param([Parameter(Mandatory = $true)][string]$DockerPath)
    try {
        $output = & $DockerPath info --format "{{.ServerVersion}}" 2>&1
        $exitCode = $LASTEXITCODE
        return [pscustomobject]@{
            Ok = ($exitCode -eq 0)
            ExitCode = $exitCode
            Detail = (($output | Out-String).Trim())
        }
    }
    catch {
        return [pscustomobject]@{
            Ok = $false
            ExitCode = -1
            Detail = $_.Exception.Message
        }
    }
}

function Assert-DockerDesktopReady {
    param([Parameter(Mandatory = $true)][string]$DockerPath)
    if ($DryRun) {
        Write-Host "[docker] dry-run: Docker Desktop readiness check skipped."
        return
    }

    $result = Test-DockerDaemon -DockerPath $DockerPath
    if ($result.Ok) {
        Write-Host "[docker] Docker daemon reachable: server $($result.Detail)"
        return
    }

    $dockerDesktopProcess = @(Get-Process -Name "Docker Desktop" -ErrorAction SilentlyContinue)
    $linuxPipe = "\\.\pipe\dockerDesktopLinuxEngine"
    $pipeExists = Test-Path -LiteralPath $linuxPipe
    $processState = if ($dockerDesktopProcess.Count -gt 0) { "running" } else { "not running" }
    $pipeState = if ($pipeExists) { "exists" } else { "missing" }

    throw @"
Docker Desktop is not ready, so Dify cannot be started.

Start Docker Desktop and wait until it says "Docker Desktop is running", then rerun:
  .\start-home-control-stack.bat -StopExisting

Diagnostics:
  docker info: $($result.Detail)
  Docker Desktop process: $processState
  Linux engine pipe: $pipeState ($linuxPipe)

If you already run Dify another way, start this script with -SkipDify.
"@
}

function Assert-VoicevoxReady {
    param([Parameter(Mandatory = $true)][string]$BaseUrl)
    if ($DryRun) {
        Write-Host "[voicevox] dry-run: VOICEVOX readiness check skipped."
        return
    }

    $normalizedBaseUrl = $BaseUrl.TrimEnd("/")
    $versionUrl = "$normalizedBaseUrl/version"
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

Then set the same value in the Dify app environment variable:
  HOME_CONTROL_API_TOKEN

This token is for Dify workflow HTTP nodes calling the local Home Assistant bridge.
"@
    }
    if ($token.Trim().Length -lt 32) {
        throw @"
HOME_CONTROL_API_TOKEN is too short for home_assistant_bridge.

Use a random 32+ character token in:
  $EnvPath

Then set the same value in the Dify app environment variable:
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

Dify should call GET /environment/current with:
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

function Assert-DifyWatcherApiKeyReady {
    param([Parameter(Mandatory = $true)][string]$EnvPath)
    if ($DryRun) {
        Write-Host "[dify] dry-run: DIFY_API_KEY check skipped."
        return
    }

    $baseUrl = (Get-DotEnvValue -Path $EnvPath -Name "DIFY_BASE_URL").Trim()
    $apiKey = (Get-DotEnvValue -Path $EnvPath -Name "DIFY_API_KEY").Trim()
    if ([string]::IsNullOrWhiteSpace($baseUrl)) {
        throw @"
DIFY_BASE_URL is missing for dify_watcher.

Set it in:
  $EnvPath

Example:
  DIFY_BASE_URL=http://127.0.0.1:8080/v1
"@
    }
    if ([string]::IsNullOrWhiteSpace($apiKey)) {
        throw @"
DIFY_API_KEY is missing for dify_watcher.

Set the Dify app API key in:
  $EnvPath

Open Dify, select the Home Control Assistant app, then copy the app API key from API Access.
"@
    }

    $parametersUrl = "$($baseUrl.TrimEnd('/'))/parameters"
    $lastError = $null
    for ($attempt = 1; $attempt -le 12; $attempt++) {
        try {
            Invoke-WebRequest `
                -Uri $parametersUrl `
                -UseBasicParsing `
                -TimeoutSec 5 `
                -Headers @{ Authorization = "Bearer $apiKey" } | Out-Null
            Write-Host "[dify] DIFY_API_KEY valid for $parametersUrl (value hidden)"
            return
        }
        catch {
            $lastError = $_
            $statusCode = $null
            if ($null -ne $_.Exception.Response) {
                try {
                    $statusCode = [int]$_.Exception.Response.StatusCode
                }
                catch {
                    $statusCode = $null
                }
            }
            if ($statusCode -eq 401 -or $statusCode -eq 403) {
                throw @"
DIFY_API_KEY is invalid for dify_watcher.

Checked:
  $parametersUrl

Update DIFY_API_KEY in:
  $EnvPath

Open Dify, select the Home Control Assistant app, then copy the current app API key from API Access.
This is different from HOME_CONTROL_API_TOKEN.
"@
            }
            Start-Sleep -Seconds 1
        }
    }

    throw @"
Dify API key check could not reach Dify API.

Checked:
  $parametersUrl

Last error:
  $($lastError.Exception.Message)

Check DIFY_BASE_URL in:
  $EnvPath

Expected format:
  http://127.0.0.1:8080/v1
"@
}

function Assert-AituberDifyApiKeyReady {
    param([Parameter(Mandatory = $true)][string]$EnvPath)
    if ($DryRun) {
        Write-Host "[aituber_kit] dry-run: DIFY_API_KEY check skipped."
        return
    }

    $selectedService = (Get-DotEnvValue -Path $EnvPath -Name "NEXT_PUBLIC_SELECT_AI_SERVICE").Trim().Trim('"')
    $apiKey = (Get-DotEnvValue -Path $EnvPath -Name "DIFY_KEY").Trim()
    if ([string]::IsNullOrWhiteSpace($apiKey)) {
        $apiKey = (Get-DotEnvValue -Path $EnvPath -Name "DIFY_API_KEY").Trim()
    }
    $baseUrl = (Get-DotEnvValue -Path $EnvPath -Name "DIFY_API_URL").Trim()
    if ([string]::IsNullOrWhiteSpace($baseUrl)) {
        $baseUrl = (Get-DotEnvValue -Path $EnvPath -Name "DIFY_URL").Trim()
    }

    if ($selectedService -ne "dify" -and [string]::IsNullOrWhiteSpace($apiKey) -and [string]::IsNullOrWhiteSpace($baseUrl)) {
        return
    }
    if ([string]::IsNullOrWhiteSpace($baseUrl)) {
        throw @"
AITuber Kit is configured for Dify, but DIFY_URL is missing.

Set it in:
  $EnvPath

Example:
  DIFY_URL=http://127.0.0.1:8080/v1
"@
    }
    if ([string]::IsNullOrWhiteSpace($apiKey)) {
        throw @"
AITuber Kit is configured for Dify, but DIFY_API_KEY is missing.

Set the Dify app API key in:
  $EnvPath

Open Dify, select the Home Control Assistant app, then copy the current app API key from API Access.
This is different from HOME_CONTROL_API_TOKEN.
"@
    }

    $parametersUrl = "$($baseUrl.TrimEnd('/'))/parameters"
    try {
        Invoke-WebRequest `
            -Uri $parametersUrl `
            -UseBasicParsing `
            -TimeoutSec 5 `
            -Headers @{ Authorization = "Bearer $apiKey" } | Out-Null
        Write-Host "[aituber_kit] DIFY_API_KEY valid for $parametersUrl (value hidden)"
    }
    catch {
        $statusCode = $null
        if ($null -ne $_.Exception.Response) {
            try {
                $statusCode = [int]$_.Exception.Response.StatusCode
            }
            catch {
                $statusCode = $null
            }
        }
        if ($statusCode -eq 401 -or $statusCode -eq 403) {
            throw @"
AITuber Kit DIFY_API_KEY is invalid.

Checked:
  $parametersUrl

Update DIFY_API_KEY in:
  $EnvPath

Open Dify, select the Home Control Assistant app, then copy the current app API key from API Access.
This key is also used by /api/difyChat.
"@
        }
        throw @"
AITuber Kit Dify API key check could not reach Dify API.

Checked:
  $parametersUrl

Last error:
  $($_.Exception.Message)

Check DIFY_URL in:
  $EnvPath
"@
    }
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

    Write-Host "Processes are already using required ports:"
    $conflicts |
        Select-Object Label, Port, PID, ProcessName, CommandLine |
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

function Wait-CameraHubStackReady {
    param(
        [Parameter(Mandatory = $true)][object]$Child,
        [int]$TimeoutSeconds = 35
    )
    $manifestPath = [string]$Child.ChildProcessFile
    if ([string]::IsNullOrWhiteSpace($manifestPath)) {
        return
    }

    Write-Host "[$($Child.Name)] waiting for Camera Hub topics readiness..."
    $deadline = [DateTimeOffset]::Now.AddSeconds($TimeoutSeconds)
    $lastDetail = "waiting for process manifest"
    while ([DateTimeOffset]::Now -lt $deadline) {
        if ($Child.Process.HasExited) {
            throw "$($Child.Name) exited before Camera Hub topics became ready. Last detail: $lastDetail"
        }

        $manifest = Read-JsonFile -Path $manifestPath
        if ($null -ne $manifest) {
            if (-not (Test-ManifestFreshForChild -Manifest $manifest -Child $Child)) {
                $lastDetail = "waiting for fresh Camera Hub process manifest"
                Start-Sleep -Milliseconds 500
                continue
            }

            $lastDetail = [string](Get-ObjectProperty -Object $manifest -Name "ready_detail" -Default "waiting for Camera Hub topics")
            $ready = [bool](Get-ObjectProperty -Object $manifest -Name "ready" -Default $false)
            if ($ready) {
                if ([string]::IsNullOrWhiteSpace($lastDetail)) {
                    $lastDetail = "ready"
                }
                Write-Host "[$($Child.Name)] Camera Hub topics ready: $lastDetail"
                return
            }
        }

        Start-Sleep -Milliseconds 500
    }

    throw "$($Child.Name) did not report Camera Hub topics ready within ${TimeoutSeconds}s. Last detail: $lastDetail"
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
        schema_version = 2
        started_at = [DateTimeOffset]::Now.ToString("o")
        workspace_root = $WorkspaceRoot
        processes = @(
            $Children | ForEach-Object {
                [pscustomobject]@{
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
            }
        )
    }
    $state | ConvertTo-Json -Depth 8 | Set-Content -LiteralPath $PidFile -Encoding UTF8
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
            -Target "http://127.0.0.1:$AituberPort" `
            -Description "会話入力、AITuber Kit の通常画面。"
        Write-GuideItem `
            -Name "Projection Visual" `
            -Target "http://127.0.0.1:$AituberPort/projection-visual" `
            -Description "投影・配信用のキャラクター表示画面。普段見るメインの表示はこちら。"
        Write-GuideItem `
            -Name "AITuber Cube Vault" `
            -Target "http://127.0.0.1:$AituberPort/cube-vault-background?fov=60&scale=1" `
            -Description "AITuber のキューブ背景確認用。必要なときだけ開く。"
    }
    if (-not $SkipDify) {
        Write-GuideItem `
            -Name "Dify" `
            -Target "http://127.0.0.1:$DifyPort" `
            -Description "Dify のワークフロー編集・ログ確認画面。Dify 本体はこのスクリプトでは停止しない。"
    }
    if (-not $SkipTouchDesignerGui) {
        Write-GuideItem `
            -Name "TD Control GUI/API" `
            -Target "http://127.0.0.1:$TouchDesignerGuiPort" `
            -Description "スタック状態、TouchDesigner UDP 連携、MediaPipe 状態の確認画面。TouchDesigner 本体ではない。"
    }

    Write-Host ""
    Write-Host "Local APIs and feeds"
    Write-Host "--------------------"
    if (-not $SkipHomeAssistantBridge) {
        Write-GuideItem `
            -Name "Home Assistant bridge health" `
            -Target "http://127.0.0.1:$HomeAssistantBridgePort/health" `
            -Description "家電操作ブリッジのヘルスチェック JSON。bind: $HomeAssistantBridgeHost"
    }
    if (-not $SkipEnvironmentState) {
        Write-GuideItem `
            -Name "Environment current state" `
            -Target "http://127.0.0.1:$EnvironmentStatePort/environment/current" `
            -Description "Dify が参照する現在状態 API。Bearer token が必要。"
        Write-GuideItem `
            -Name "Environment indicators" `
            -Target "http://127.0.0.1:$EnvironmentStatePort/indicators/current" `
            -Description "HUD/Cube 背景向けのローカル限定・表示用状態 API。"
    }
    if (-not $SkipMediapipe -and $mediapipeMediaMtxStackLaunched) {
        $browserMonitorPath = Join-Path $MediapipeRoot "apps\browser_camera_hub_viewer.html"
        $browserMonitorFile = $browserMonitorPath -replace "\\", "/"
        $encodedMediaUrl = "http%3A%2F%2F127.0.0.1%3A8889%2Fcam0%3Fcontrols%3Dfalse%26muted%3Dtrue%26autoplay%3Dtrue"
        $encodedWsUrl = "ws%3A%2F%2F127.0.0.1%3A$MediapipePort"
        $browserMonitorUrl = "file:///{0}?mediaUrl={1}{2}wsUrl={3}" -f $browserMonitorFile, $encodedMediaUrl, ([char]38), $encodedWsUrl
        Write-GuideItem `
            -Name "MediaPipe Browser Monitor" `
            -Target $browserMonitorUrl `
            -Description "MediaMTX の映像と Camera Hub の topic を同時に見るブラウザ GUI。必要なときだけ手動で開く。"
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
    if (-not $SkipDifyWatch) {
        Write-GuideItem `
            -Name "Dify watcher" `
            -Target "no browser URL" `
            -Description "Dify のストリームを AITuber の発話キューへ渡す常駐処理。"
    }
    Write-GuideItem `
        -Name "TouchDesigner UDP receiver" `
        -Target "127.0.0.1:9001" `
        -Description "TouchDesigner 側が受け取る UDP 宛先。このスクリプトは TouchDesigner 本体を起動しない。"

    Write-Host ""
    Write-Host "Commands"
    Write-Host "--------"
    Write-Host "  Status : .\status-home-control-stack.bat"
    Write-Host "  Stop   : Ctrl+C in this terminal, or .\stop-home-control-stack.bat"
    Write-Host "  Note   : Dify is external; use the stop script with -StopDify only when you intend to stop Dify too."
    Write-Host ""
}

function Invoke-External {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [string[]]$Arguments = @(),
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string]$Label
    )
    Write-Host "[$Label] $(Format-CommandLine -Command (@($FilePath) + $Arguments))"
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
        [string]$ChildProcessFile = ""
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
    $startInfo.Environment["PYTHONUTF8"] = "1"
    $startInfo.Environment["PYTHONIOENCODING"] = "utf-8"
    $startInfo.Environment["NO_COLOR"] = "1"
    $startInfo.Environment["FORCE_COLOR"] = "0"
    $startInfo.Environment["TERM"] = "dumb"
    $startInfo.Environment["HOME_CONTROL_WORKSPACE_ROOT"] = $WorkspaceRoot
    $startInfo.Environment["HOME_CONTROL_STACK_STATE_DIR"] = $StateDir
    $startInfo.Environment["MEDIAPIPE_PORT"] = [string]$MediapipePort
    $startInfo.Environment["ENVIRONMENT_STATE_PORT"] = [string]$EnvironmentStatePort
    $startInfo.Environment["TOUCHDESIGNER_GUI_PORT"] = [string]$TouchDesignerGuiPort
    $startInfo.Environment["TOUCHDESIGNER_UDP_HOST"] = "127.0.0.1"
    $startInfo.Environment["TOUCHDESIGNER_UDP_PORT"] = "9001"
    foreach ($key in $Spec.Environment.Keys) {
        $startInfo.Environment[$key] = [string]$Spec.Environment[$key]
    }

    $process = [System.Diagnostics.Process]::new()
    $process.StartInfo = $startInfo
    $process.EnableRaisingEvents = $true
    if (-not $process.Start()) {
        throw "failed to start $($Spec.Name)"
    }

    $stdoutEvent = Register-ObjectEvent `
        -InputObject $process `
        -EventName OutputDataReceived `
        -MessageData @{ Prefix = $Spec.Name } `
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
        -MessageData @{ Prefix = $Spec.Name } `
        -Action {
            $line = $EventArgs.Data
            if ($null -ne $line) {
                $cleanLine = [regex]::Replace($line, "`e\[[0-?]*[ -/]*[@-~]", "")
                [Console]::Error.WriteLine("[{0}] {1}", $Event.MessageData.Prefix, $cleanLine)
            }
        }

    $process.BeginOutputReadLine()
    $process.BeginErrorReadLine()
    $commandLine = Format-CommandLine -Command (@($Spec.FilePath) + $Spec.Arguments)
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
        NotifiedExit = $false
    }
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
        & $StopScript -WorkspaceRoot $WorkspaceRoot -Force
    }
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
if (-not $SkipDifyWatch) {
    Assert-Directory -Path $DifyWatchRoot -Label "sword-voice-agent"
    if (-not (Test-Path -LiteralPath $DifyWatchScript -PathType Leaf)) {
        throw "Dify watcher script not found: $DifyWatchScript"
    }
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

if (-not $DryRun) {
    $requiredPorts = @()
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
    if ($requiredPorts.Count -gt 0) {
        Resolve-PortConflicts -PortSpecs $requiredPorts
    }
}

$uv = Resolve-Tool -Name "uv"
$npm = Resolve-Tool -Name "npm"
$node = $null
if (-not $SkipTouchDesignerGui) {
    $node = Resolve-Tool -Name "node"
}
$powerShell = $null
if (-not $SkipDifyWatch) {
    $powerShell = Resolve-CurrentPowerShell
}
$docker = $null
if (-not $SkipDify) {
    $docker = Resolve-Tool -Name "docker"
}

if (-not $SkipDify) {
    Assert-DockerDesktopReady -DockerPath $docker
}

if (-not $SkipVoicevoxCheck -and -not $SkipAituber) {
    Assert-VoicevoxReady -BaseUrl $VoicevoxUrl
}

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

if (-not $SkipDify) {
    if (-not (Test-Path -LiteralPath $DifyDockerRoot -PathType Container)) {
        throw "Dify docker directory not found: $DifyDockerRoot. Use -SkipDify to skip the Dify check/start."
    }
    $difyUrl = "http://127.0.0.1:$DifyPort"
    if (Test-HttpReachable -Url $difyUrl) {
        Write-Host "[dify] reachable: $difyUrl"
    }
    else {
        Invoke-External `
            -FilePath $docker `
            -Arguments @("compose", "up", "-d") `
            -WorkingDirectory $DifyDockerRoot `
            -Label "dify"
        Write-Host "[dify] started with docker compose. UI: $difyUrl"
    }
}

if (-not $SkipDifyWatch) {
    Assert-DifyWatcherApiKeyReady -EnvPath $DifyWatchEnvPath
    Write-Host "[dify] Dify app env HOME_CONTROL_API_TOKEN must match home-assistant-server\.env (not readable from dify_watcher .env)"
}

if (-not $SkipAituber) {
    Assert-AituberDifyApiKeyReady -EnvPath (Join-Path $AituberRoot ".env")
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
$mediapipeLegacyWebSocketLaunched = $false
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
        "127.0.0.1",
        "--port",
        [string]$EnvironmentStatePort,
        "--ha-events-path",
        (Join-Path $HomeAssistantServerRoot ".cache\home_control\events.jsonl"),
        "--state-query-feedback-path",
        $StateQueryFeedbackPath,
        "--camera-hub-url",
        "ws://127.0.0.1:$MediapipePort",
        "--home-assistant-health-url",
        "http://127.0.0.1:$HomeAssistantBridgePort/health",
        "--aituber-url",
        "http://127.0.0.1:$AituberPort",
        "--dify-url",
        "http://127.0.0.1:$DifyPort",
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
if (-not $SkipMediapipe) {
    $cameraHubServerPath = Join-Path $MediapipeRoot "apps\serve_camera_hub.py"
    $cameraHubGuiPath = Join-Path $MediapipeRoot "apps\camera_hub_gui.py"
    $cameraHubStackPath = Join-Path $MediapipeRoot "scripts\camera_hub_stack.py"
    $legacyWebSocketPath = Join-Path $MediapipeRoot "apps\serve_websocket.py"

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
            "--hub-port",
            [string]$MediapipePort
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
    else {
        if (-not (Test-Path -LiteralPath $legacyWebSocketPath -PathType Leaf)) {
            throw "No compatible MediaPipe entrypoint found. Missing: apps\serve_camera_hub.py and apps\serve_websocket.py"
        }
        $specs += New-ServiceSpec `
            -Name "mediapipe_ws" `
            -FilePath $uv `
            -Arguments @(
                "run",
                "python",
                "apps\serve_websocket.py",
                "--host",
                "127.0.0.1",
                "--port",
                [string]$MediapipePort
            ) `
            -WorkingDirectory $MediapipeRoot `
            -Module "mediapipe-sword-sign" `
            -Role "legacy_websocket" `
            -AllowedProcessNames @("uv", "python")
        $mediapipeLegacyWebSocketLaunched = $true
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
        -Module "aituber-kit" `
        -Role "frontend" `
        -AllowedProcessNames @("cmd", "node", "npm")
}
if (-not $SkipDifyWatch) {
    $specs += New-ServiceSpec `
        -Name "dify_watcher" `
        -FilePath $powerShell `
        -Arguments @(
            "-NoLogo",
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            $DifyWatchScript,
            "-EnvPath",
            $DifyWatchEnvPath,
            "-StatusDir",
            $DifyWatchStatusDir
        ) `
        -WorkingDirectory $DifyWatchRoot `
        -Module "sword-voice-agent" `
        -Role "dify_watcher" `
        -AllowedProcessNames @("pwsh", "powershell", "python")
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
            $TouchDesignerGuiHost
        ) `
        -WorkingDirectory $TouchDesignerGuiToolsRoot `
        -Module "touchdesigner-ai-controller" `
        -Role "display_runtime" `
        -AllowedProcessNames @("node")
}

if ($DryRun) {
    foreach ($spec in $specs) {
        Write-Host "[$($spec.Name)] $(Format-CommandLine -Command (@($spec.FilePath) + $spec.Arguments))"
    }
    return
}

$children = @()
$shutdownStarted = $false
$exitCode = 0

try {
    foreach ($spec in $specs) {
        $children += Start-SupervisedProcess -Spec $spec
        Save-PidState -Children $children
        if ($spec.Name -eq "mediapipe_camera_hub_stack") {
            Wait-CameraHubStackReady -Child $children[-1]
        }
        Start-Sleep -Milliseconds 500
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
finally {
    if (-not $shutdownStarted) {
        $liveChildren = @($children | Where-Object { -not $_.Process.HasExited })
        if ($liveChildren.Count -gt 0) {
            $shutdownStarted = $true
            Stop-RecordedStack
        }
    }
    Stop-SupervisedEvents -Children $children
}

exit $exitCode
