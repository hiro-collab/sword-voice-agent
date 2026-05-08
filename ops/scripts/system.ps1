param(
    [Parameter(Position = 0)]
    [ValidateSet("status", "start", "stop")]
    [string]$Command = "status",
    [string]$Profile = "full-local",
    [string]$WorkspaceRoot = "",
    [string]$StackStateDir = "",
    [string]$HomeControlConfigPath = "",
    [string]$DifyDockerRoot = "",
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
    [int]$DifyPort = 8080,
    [string]$ThoughtCoreHost = "127.0.0.1",
    [int]$ThoughtCorePort = 18787,
    [string]$VoicevoxUrl = "",
    [ValidateSet("gui", "headless", "camera-hub", "mediamtx")]
    [string]$MediapipeMode = "mediamtx",
    [string]$MediapipeCameraName = "HD Pro Webcam C920",
    [switch]$MediapipeOpenBrowser,
    [switch]$MediapipeNoBrowser,
    [switch]$MediapipePythonGui,
    [switch]$SkipDify,
    [switch]$SkipHomeAssistantBridge,
    [switch]$SkipEnvironmentState,
    [switch]$SkipMediapipe,
    [switch]$SkipVisionSnapshotProcessor,
    [switch]$SkipAituber,
    [switch]$SkipDifyWatch,
    [switch]$SkipTouchDesignerGui,
    [switch]$EnableThoughtCore,
    [switch]$EnableThoughtCoreWatch,
    [switch]$StopExisting,
    [switch]$SkipVoicevoxCheck,
    [switch]$EnableHomeControlFaultInjection,
    [switch]$StopDify,
    [switch]$Force,
    [switch]$DryRun,
    [switch]$ManifestOnly,
    [switch]$Watch,
    [int]$IntervalSeconds = 3
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
[Console]::InputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

function Resolve-RepoRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
}

function Resolve-WorkspaceRoot {
    param([string]$Value = "")
    if (-not [string]::IsNullOrWhiteSpace($Value)) {
        return (Resolve-Path -LiteralPath $Value).Path
    }
    return (Resolve-Path -LiteralPath (Join-Path (Resolve-RepoRoot) "..")).Path
}

function Resolve-StackStateDir {
    param(
        [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
        [string]$Value = ""
    )
    if ([string]::IsNullOrWhiteSpace($Value)) {
        $Value = [Environment]::GetEnvironmentVariable("HOME_CONTROL_STACK_STATE_DIR")
    }
    if ([string]::IsNullOrWhiteSpace($Value)) {
        return Join-Path $WorkspaceRoot ".cache\home-control-stack"
    }
    if ([System.IO.Path]::IsPathRooted($Value)) {
        return $Value
    }
    return Join-Path $WorkspaceRoot $Value
}

function Resolve-CurrentPowerShell {
    $pwsh = Get-Command "pwsh" -ErrorAction SilentlyContinue
    if ($null -ne $pwsh) {
        return $pwsh.Source
    }
    $currentProcess = Get-Process -Id $PID -ErrorAction SilentlyContinue
    if ($null -ne $currentProcess -and -not [string]::IsNullOrWhiteSpace($currentProcess.Path)) {
        return $currentProcess.Path
    }
    return (Get-Command "powershell" -ErrorAction Stop).Source
}

function Read-JsonObject {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        throw "JSON file not found: $Path"
    }
    return Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
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
    if ($Value -is [string]) {
        if ([string]::IsNullOrWhiteSpace($Value)) {
            return @()
        }
        return @($Value)
    }
    $items = @()
    foreach ($item in @($Value)) {
        if ($null -ne $item) {
            $text = [string]$item
            if (-not [string]::IsNullOrWhiteSpace($text)) {
                $items += $text
            }
        }
    }
    return $items
}

function Read-PidMap {
    param([Parameter(Mandatory = $true)][string]$Path)
    $map = @{}
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $map
    }
    try {
        $raw = Read-JsonObject -Path $Path
        foreach ($entry in @(Get-ObjectProperty -Object $raw -Name "processes" -Default @())) {
            $name = [string](Get-ObjectProperty -Object $entry -Name "name" -Default "")
            if (-not [string]::IsNullOrWhiteSpace($name)) {
                $map[$name] = $entry
            }
        }
    }
    catch {
        Write-Warning "Failed to read PID registry: $Path"
    }
    return $map
}

function Test-PidEntryAlive {
    param([object]$Entry)
    $pidValue = [int](Get-ObjectProperty -Object $Entry -Name "pid" -Default 0)
    if ($pidValue -le 0) {
        return $false
    }
    try {
        $process = Get-Process -Id $pidValue -ErrorAction Stop
        $startedAt = [string](Get-ObjectProperty -Object $Entry -Name "started_at" -Default "")
        if ([string]::IsNullOrWhiteSpace($startedAt)) {
            return $true
        }
        $recorded = [DateTimeOffset]::Parse($startedAt)
        $processStarted = [DateTimeOffset]$process.StartTime
        return (
            $processStarted -ge $recorded.AddSeconds(-10) -and
            $processStarted -le $recorded.AddSeconds(60)
        )
    }
    catch {
        return $false
    }
}

function Get-ServiceState {
    param(
        [object]$Manifest,
        [hashtable]$PidMap
    )
    $pidNames = ConvertTo-StringArray -Value (Get-ObjectProperty -Object $Manifest -Name "pid_names" -Default @())
    $recorded = @()
    foreach ($pidName in $pidNames) {
        if ($PidMap.ContainsKey($pidName)) {
            $recorded += $PidMap[$pidName]
        }
    }
    if ($recorded.Count -eq 0) {
        return "unrecorded"
    }
    foreach ($entry in $recorded) {
        if (Test-PidEntryAlive -Entry $entry) {
            return "running"
        }
    }
    return "stopped"
}

function Test-ServiceSelected {
    param(
        [string[]]$Services,
        [Parameter(Mandatory = $true)][string]$ServiceId
    )
    return $Services -contains $ServiceId
}

function Resolve-EffectiveServices {
    param([Parameter(Mandatory = $true)][string[]]$Services)
    $selected = @{}
    foreach ($service in $Services) {
        $selected[$service] = $true
    }

    if ($SkipDify) { $selected["dify_stack"] = $false }
    if ($SkipHomeAssistantBridge) { $selected["home_assistant_bridge"] = $false }
    if ($SkipEnvironmentState) { $selected["environment_state_server"] = $false }
    if ($SkipMediapipe) { $selected["mediapipe_camera_hub_stack"] = $false }
    if ($SkipVisionSnapshotProcessor) { $selected["vision_snapshot_processor"] = $false }
    if ($SkipAituber) { $selected["aituber_kit"] = $false }
    if ($SkipDifyWatch) { $selected["dify_watcher"] = $false }
    if ($SkipTouchDesignerGui) { $selected["touchdesigner_control_gui"] = $false }
    if ($EnableThoughtCore) { $selected["thought_core_api"] = $true }
    if ($EnableThoughtCoreWatch) { $selected["thought_core_watcher"] = $true }

    return [string[]]@(
        $selected.Keys |
            Where-Object { $selected[$_] -eq $true } |
            Sort-Object
    )
}

function Add-ArgumentIf {
    param(
        [System.Collections.Generic.List[string]]$Arguments,
        [bool]$Condition,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if ($Condition) {
        $Arguments.Add($Name)
    }
}

function Add-NamedArgument {
    param(
        [System.Collections.Generic.List[string]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Name,
        [object]$Value,
        [bool]$SkipWhenBlank = $false
    )
    if ($SkipWhenBlank -and [string]::IsNullOrWhiteSpace([string]$Value)) {
        return
    }
    $Arguments.Add($Name)
    $Arguments.Add([string]$Value)
}

function New-CommonStackArguments {
    param(
        [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
        [Parameter(Mandatory = $true)][string]$StackStateDir
    )
    $arguments = [System.Collections.Generic.List[string]]::new()
    Add-NamedArgument -Arguments $arguments -Name "-WorkspaceRoot" -Value $WorkspaceRoot
    Add-NamedArgument -Arguments $arguments -Name "-StackStateDir" -Value $StackStateDir
    return [string[]]$arguments.ToArray()
}

function Add-CommonPortArguments {
    param([System.Collections.Generic.List[string]]$Arguments)
    Add-NamedArgument -Arguments $Arguments -Name "-HomeAssistantBridgePort" -Value $HomeAssistantBridgePort
    Add-NamedArgument -Arguments $Arguments -Name "-EnvironmentStatePort" -Value $EnvironmentStatePort
    Add-NamedArgument -Arguments $Arguments -Name "-MediapipePort" -Value $MediapipePort
    Add-NamedArgument -Arguments $Arguments -Name "-VisionSnapshotProcessorPort" -Value $VisionSnapshotProcessorPort
    Add-NamedArgument -Arguments $Arguments -Name "-AituberPort" -Value $AituberPort
    Add-NamedArgument -Arguments $Arguments -Name "-TouchDesignerGuiPort" -Value $TouchDesignerGuiPort
    Add-NamedArgument -Arguments $Arguments -Name "-DifyPort" -Value $DifyPort
    Add-NamedArgument -Arguments $Arguments -Name "-ThoughtCorePort" -Value $ThoughtCorePort
}

function New-StackStartArguments {
    param(
        [Parameter(Mandatory = $true)][string[]]$Services,
        [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
        [Parameter(Mandatory = $true)][string]$StackStateDir
    )
    $arguments = [System.Collections.Generic.List[string]]::new()
    foreach ($argument in (New-CommonStackArguments -WorkspaceRoot $WorkspaceRoot -StackStateDir $StackStateDir)) {
        $arguments.Add($argument)
    }
    Add-NamedArgument -Arguments $arguments -Name "-HomeAssistantBridgePort" -Value $HomeAssistantBridgePort
    Add-NamedArgument -Arguments $arguments -Name "-HomeAssistantBridgeHost" -Value $HomeAssistantBridgeHost
    Add-NamedArgument -Arguments $arguments -Name "-HomeControlConfigPath" -Value $HomeControlConfigPath -SkipWhenBlank $true
    Add-NamedArgument -Arguments $arguments -Name "-EnvironmentStatePort" -Value $EnvironmentStatePort
    Add-NamedArgument -Arguments $arguments -Name "-MediapipePort" -Value $MediapipePort
    Add-NamedArgument -Arguments $arguments -Name "-MediapipeBrowserMonitorPort" -Value $MediapipeBrowserMonitorPort
    Add-NamedArgument -Arguments $arguments -Name "-VisionSnapshotProcessorPort" -Value $VisionSnapshotProcessorPort
    Add-NamedArgument -Arguments $arguments -Name "-AituberPort" -Value $AituberPort
    Add-NamedArgument -Arguments $arguments -Name "-AituberHost" -Value $AituberHost
    Add-NamedArgument -Arguments $arguments -Name "-TouchDesignerGuiPort" -Value $TouchDesignerGuiPort
    Add-NamedArgument -Arguments $arguments -Name "-TouchDesignerGuiHost" -Value $TouchDesignerGuiHost
    Add-NamedArgument -Arguments $arguments -Name "-DifyPort" -Value $DifyPort
    Add-NamedArgument -Arguments $arguments -Name "-DifyDockerRoot" -Value $DifyDockerRoot -SkipWhenBlank $true
    Add-NamedArgument -Arguments $arguments -Name "-ThoughtCoreHost" -Value $ThoughtCoreHost
    Add-NamedArgument -Arguments $arguments -Name "-ThoughtCorePort" -Value $ThoughtCorePort
    Add-NamedArgument -Arguments $arguments -Name "-VoicevoxUrl" -Value $VoicevoxUrl -SkipWhenBlank $true
    Add-NamedArgument -Arguments $arguments -Name "-MediapipeMode" -Value $MediapipeMode
    Add-NamedArgument -Arguments $arguments -Name "-MediapipeCameraName" -Value $MediapipeCameraName

    Add-ArgumentIf -Arguments $arguments -Condition (-not (Test-ServiceSelected -Services $Services -ServiceId "dify_stack")) -Name "-SkipDify"
    Add-ArgumentIf -Arguments $arguments -Condition (-not (Test-ServiceSelected -Services $Services -ServiceId "home_assistant_bridge")) -Name "-SkipHomeAssistantBridge"
    Add-ArgumentIf -Arguments $arguments -Condition (-not (Test-ServiceSelected -Services $Services -ServiceId "environment_state_server")) -Name "-SkipEnvironmentState"
    Add-ArgumentIf -Arguments $arguments -Condition (-not (Test-ServiceSelected -Services $Services -ServiceId "mediapipe_camera_hub_stack")) -Name "-SkipMediapipe"
    Add-ArgumentIf -Arguments $arguments -Condition (-not (Test-ServiceSelected -Services $Services -ServiceId "vision_snapshot_processor")) -Name "-SkipVisionSnapshotProcessor"
    Add-ArgumentIf -Arguments $arguments -Condition (-not (Test-ServiceSelected -Services $Services -ServiceId "aituber_kit")) -Name "-SkipAituber"
    Add-ArgumentIf -Arguments $arguments -Condition (-not (Test-ServiceSelected -Services $Services -ServiceId "dify_watcher")) -Name "-SkipDifyWatch"
    Add-ArgumentIf -Arguments $arguments -Condition (-not (Test-ServiceSelected -Services $Services -ServiceId "touchdesigner_control_gui")) -Name "-SkipTouchDesignerGui"
    Add-ArgumentIf -Arguments $arguments -Condition (Test-ServiceSelected -Services $Services -ServiceId "thought_core_api") -Name "-EnableThoughtCore"
    Add-ArgumentIf -Arguments $arguments -Condition (Test-ServiceSelected -Services $Services -ServiceId "thought_core_watcher") -Name "-EnableThoughtCoreWatch"

    Add-ArgumentIf -Arguments $arguments -Condition $MediapipeOpenBrowser.IsPresent -Name "-MediapipeOpenBrowser"
    Add-ArgumentIf -Arguments $arguments -Condition $MediapipeNoBrowser.IsPresent -Name "-MediapipeNoBrowser"
    Add-ArgumentIf -Arguments $arguments -Condition $MediapipePythonGui.IsPresent -Name "-MediapipePythonGui"
    Add-ArgumentIf -Arguments $arguments -Condition $StopExisting.IsPresent -Name "-StopExisting"
    Add-ArgumentIf -Arguments $arguments -Condition $SkipVoicevoxCheck.IsPresent -Name "-SkipVoicevoxCheck"
    Add-ArgumentIf -Arguments $arguments -Condition $EnableHomeControlFaultInjection.IsPresent -Name "-EnableHomeControlFaultInjection"
    Add-ArgumentIf -Arguments $arguments -Condition $DryRun.IsPresent -Name "-DryRun"
    return [string[]]$arguments.ToArray()
}

function New-StackStatusArguments {
    param(
        [Parameter(Mandatory = $true)][string[]]$Services,
        [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
        [Parameter(Mandatory = $true)][string]$StackStateDir
    )
    $arguments = [System.Collections.Generic.List[string]]::new()
    foreach ($argument in (New-CommonStackArguments -WorkspaceRoot $WorkspaceRoot -StackStateDir $StackStateDir)) {
        $arguments.Add($argument)
    }
    Add-CommonPortArguments -Arguments $arguments
    Add-NamedArgument -Arguments $arguments -Name "-VoicevoxUrl" -Value $VoicevoxUrl -SkipWhenBlank $true
    Add-ArgumentIf -Arguments $arguments -Condition (Test-ServiceSelected -Services $Services -ServiceId "thought_core_api") -Name "-EnableThoughtCore"
    Add-ArgumentIf -Arguments $arguments -Condition (Test-ServiceSelected -Services $Services -ServiceId "thought_core_watcher") -Name "-EnableThoughtCoreWatch"
    Add-ArgumentIf -Arguments $arguments -Condition $Watch.IsPresent -Name "-Watch"
    Add-NamedArgument -Arguments $arguments -Name "-IntervalSeconds" -Value $IntervalSeconds
    return [string[]]$arguments.ToArray()
}

function New-StackStopArguments {
    param(
        [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
        [Parameter(Mandatory = $true)][string]$StackStateDir
    )
    $arguments = [System.Collections.Generic.List[string]]::new()
    foreach ($argument in (New-CommonStackArguments -WorkspaceRoot $WorkspaceRoot -StackStateDir $StackStateDir)) {
        $arguments.Add($argument)
    }
    Add-NamedArgument -Arguments $arguments -Name "-DifyDockerRoot" -Value $DifyDockerRoot -SkipWhenBlank $true
    Add-ArgumentIf -Arguments $arguments -Condition $StopDify.IsPresent -Name "-StopDify"
    Add-ArgumentIf -Arguments $arguments -Condition $Force.IsPresent -Name "-Force"
    Add-ArgumentIf -Arguments $arguments -Condition $DryRun.IsPresent -Name "-DryRun"
    return [string[]]$arguments.ToArray()
}

function Invoke-StackScript {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptName,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$Operation
    )
    $scriptPath = Join-Path (Resolve-RepoRoot) "ops\scripts\home-control-stack\$ScriptName"
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        throw "Stack script not found: $scriptPath"
    }
    $powerShell = Resolve-CurrentPowerShell
    Write-Host ("[ops] delegate={0} layer=ops script={1}" -f $Operation, $scriptPath)
    if ($DryRun) {
        Write-Host ("[ops] delegate_args={0}" -f ($Arguments -join " "))
    }
    & $powerShell -NoLogo -NoProfile -ExecutionPolicy Bypass -File $scriptPath @Arguments
    $exitCode = if ($null -eq $LASTEXITCODE) { 0 } else { [int]$LASTEXITCODE }
    if ($exitCode -ne 0) {
        exit $exitCode
    }
}

function Write-ManifestStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Profile,
        [Parameter(Mandatory = $true)][string]$StackStateDir,
        [Parameter(Mandatory = $true)][string]$ManifestRoot,
        [Parameter(Mandatory = $true)][string[]]$Services
    )
    $pidFile = Join-Path $StackStateDir "pids.json"
    $pidMap = Read-PidMap -Path $pidFile

    Write-Host ("[ops] command=status profile={0} state_dir={1}" -f $Profile, $StackStateDir)
    foreach ($serviceId in $Services) {
        $servicePath = Join-Path $ManifestRoot "services\$serviceId.json"
        $manifest = Read-JsonObject -Path $servicePath
        $layer = [string](Get-ObjectProperty -Object $manifest -Name "layer" -Default "ops")
        $logical = [string](Get-ObjectProperty -Object $manifest -Name "logical_service" -Default "")
        $state = Get-ServiceState -Manifest $manifest -PidMap $pidMap
        $pidNames = ConvertTo-StringArray -Value (Get-ObjectProperty -Object $manifest -Name "pid_names" -Default @())
        Write-Host (
            "[ops] service={0} layer={1} logical={2} state={3} pid_names={4}" -f
            $serviceId,
            $layer,
            $logical,
            $state,
            ($pidNames -join ",")
        )
    }
}

$repoRoot = Resolve-RepoRoot
$workspaceRoot = Resolve-WorkspaceRoot -Value $WorkspaceRoot
$stackStateDir = Resolve-StackStateDir -WorkspaceRoot $workspaceRoot -Value $StackStateDir
$manifestRoot = Join-Path $repoRoot "ops\manifests"
$profilePath = Join-Path $manifestRoot "profiles\$Profile.json"
$profileManifest = Read-JsonObject -Path $profilePath
$services = @(Resolve-EffectiveServices -Services @(ConvertTo-StringArray -Value (Get-ObjectProperty -Object $profileManifest -Name "services" -Default @())))

if ($services.Count -eq 0) {
    throw "Profile has no services: $Profile"
}

switch ($Command) {
    "start" {
        $arguments = New-StackStartArguments -Services $services -WorkspaceRoot $workspaceRoot -StackStateDir $stackStateDir
        Write-Host ("[ops] command=start profile={0} state_dir={1}" -f $Profile, $stackStateDir)
        Invoke-StackScript -ScriptName "start-home-control-stack.ps1" -Arguments $arguments -Operation "start"
    }
    "stop" {
        $arguments = New-StackStopArguments -WorkspaceRoot $workspaceRoot -StackStateDir $stackStateDir
        Write-Host ("[ops] command=stop profile={0} state_dir={1}" -f $Profile, $stackStateDir)
        Invoke-StackScript -ScriptName "stop-home-control-stack.ps1" -Arguments $arguments -Operation "stop"
    }
    default {
        Write-ManifestStatus -Profile $Profile -StackStateDir $stackStateDir -ManifestRoot $manifestRoot -Services $services
        if (-not $ManifestOnly) {
            $arguments = New-StackStatusArguments -Services $services -WorkspaceRoot $workspaceRoot -StackStateDir $stackStateDir
            Invoke-StackScript -ScriptName "status-home-control-stack.ps1" -Arguments $arguments -Operation "status"
        }
    }
}
