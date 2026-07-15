param(
    [string]$WorkspaceRoot = "",
    [string]$StackStateDir = "",
    [int]$HomeAssistantBridgePort = 8787,
    [int]$EnvironmentStatePort = 8790,
    [int]$MediapipePort = 8765,
    [int]$VisionSnapshotProcessorPort = 8776,
    [int]$AituberPort = 3000,
    [int]$TouchDesignerGuiPort = 8788,
    [int]$ThoughtCorePort = 18787,
    [string]$VoicevoxUrl = "",
    [switch]$EnableThoughtCore,
    [switch]$EnableThoughtCoreWatch,
    [switch]$Watch,
    [int]$IntervalSeconds = 3
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

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
$PidFile = Join-Path $StackStateDir "pids.json"
$CameraHubStateFile = Join-Path $StackStateDir "modules\mediapipe_camera_hub_stack\processes.json"
$AituberEnvPath = Join-Path $WorkspaceRoot "organs\expression\aituber-kit\.env"

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)\s*$") {
            return $matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

function Read-PidState {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
        return @{}
    }
    try {
        $raw = Get-Content -LiteralPath $PidFile -Raw -Encoding UTF8 | ConvertFrom-Json
        $map = @{
            __registry_schema_valid = ((Get-ObjectProperty -Object $raw -Name "schema_version" -Default 0) -eq 3)
            __registry_duplicate_names = $false
        }
        foreach ($entry in @($raw.processes)) {
            $name = [string](Get-ObjectProperty -Object $entry -Name "name" -Default "")
            if ([string]::IsNullOrWhiteSpace($name) -or $map.ContainsKey($name)) {
                $map["__registry_duplicate_names"] = $true
                continue
            }
            $map[$name] = $entry
        }
        return $map
    }
    catch {
        return @{}
    }
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

function Test-ProcessAlive {
    param([object]$Entry)
    if ($null -eq $Entry) {
        return $false
    }
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
        return $true
    }
    $processName = Normalize-ProcessName -Name ([string]$process.ProcessName)
    $allowed = @($allowedNames | ForEach-Object { Normalize-ProcessName -Name $_ })
    return $allowed -contains $processName
}

function Test-TcpListen {
    param([Parameter(Mandatory = $true)][int]$Port)
    $listeners = @(Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue)
    return $listeners.Count -gt 0
}

function Invoke-HttpCheck {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 2
    )
    try {
        $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds
        return [pscustomobject]@{
            Ok = $true
            Detail = "HTTP $([int]$response.StatusCode)"
        }
    }
    catch {
        return [pscustomobject]@{
            Ok = $false
            Detail = $_.Exception.Message
        }
    }
}

function Invoke-JsonHealthCheck {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutSeconds = 2
    )
    try {
        $response = Invoke-RestMethod -Uri $Url -TimeoutSec $TimeoutSeconds
        $ok = $false
        if ($null -ne $response.ok) {
            $ok = [bool]$response.ok
        }
        return [pscustomobject]@{
            Ok = $ok
            Detail = ($response | ConvertTo-Json -Compress -Depth 6)
        }
    }
    catch {
        return [pscustomobject]@{
            Ok = $false
            Detail = $_.Exception.Message
        }
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

function Test-CameraHubRequiredProcessesRunning {
    param([Parameter(Mandatory = $true)][object]$Manifest)
    $runningByName = @{}
    foreach ($process in @(Get-ObjectProperty -Object $Manifest -Name "processes" -Default @())) {
        $name = [string](Get-ObjectProperty -Object $process -Name "name" -Default "")
        $running = Get-ObjectProperty -Object $process -Name "running" -Default $null
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

function Test-CameraHubRegistryEntry {
    param(
        [Parameter(Mandatory = $true)][hashtable]$PidState,
        [object]$Entry
    )
    if (
        $null -eq $Entry -or
        $PidState["__registry_schema_valid"] -ne $true -or
        $PidState["__registry_duplicate_names"] -eq $true -or
        [string](Get-ObjectProperty -Object $Entry -Name "name" -Default "") -ne "mediapipe_camera_hub_stack" -or
        [string](Get-ObjectProperty -Object $Entry -Name "module" -Default "") -ne "mediapipe-sword-sign" -or
        [string](Get-ObjectProperty -Object $Entry -Name "role" -Default "") -ne "camera_hub_stack" -or
        [string](Get-ObjectProperty -Object $Entry -Name "stop_strategy" -Default "") -ne "managed_tree"
    ) {
        return $false
    }
    $pidValue = 0
    $pidText = [string](Get-ObjectProperty -Object $Entry -Name "pid" -Default "")
    if (-not [int]::TryParse($pidText, [ref]$pidValue) -or $pidValue -le 0) {
        return $false
    }
    $startedAtText = [string](Get-ObjectProperty -Object $Entry -Name "started_at" -Default "")
    $startedAtValue = [DateTimeOffset]::MinValue
    if ([string]::IsNullOrWhiteSpace($startedAtText) -or -not [DateTimeOffset]::TryParse($startedAtText, [ref]$startedAtValue)) {
        return $false
    }
    try {
        if (-not [System.IO.Path]::GetFullPath([string](Get-ObjectProperty -Object $Entry -Name "child_process_file" -Default "")).Equals(
            [System.IO.Path]::GetFullPath($CameraHubStateFile),
            [System.StringComparison]::OrdinalIgnoreCase
        )) {
            return $false
        }
    }
    catch {
        return $false
    }
    $allowedNames = @(ConvertTo-StringArray -Value (Get-ObjectProperty -Object $Entry -Name "allowed_process_names" -Default @()) |
        ForEach-Object { Normalize-ProcessName -Name $_ } |
        Sort-Object -Unique)
    if (($allowedNames -join ",") -ne "ffmpeg,mediamtx,python,uv") {
        return $false
    }
    return Test-ProcessAlive -Entry $Entry
}

function Test-CameraHubRuntimeOwnership {
    param(
        [Parameter(Mandatory = $true)][object]$Manifest,
        [Parameter(Mandatory = $true)][object]$Entry,
        [Parameter(Mandatory = $true)][int]$Port
    )
    $rootPid = [int](Get-ObjectProperty -Object $Entry -Name "pid" -Default 0)
    $ownerPid = 0
    if ($rootPid -le 0 -or -not [int]::TryParse([string](Get-ObjectProperty -Object $Manifest -Name "owner_pid" -Default ""), [ref]$ownerPid) -or $ownerPid -le 0) {
        return $false
    }
    $root = Get-Process -Id $rootPid -ErrorAction SilentlyContinue
    $owner = Get-Process -Id $ownerPid -ErrorAction SilentlyContinue
    if (
        $null -eq $root -or $null -eq $owner -or
        -not (Test-ProcessStartTimeMatches -Process $root -RecordedAt ([string](Get-ObjectProperty -Object $Entry -Name "started_at" -Default "")) -GraceSeconds 60) -or
        (Normalize-ProcessName -Name ([string]$owner.ProcessName)) -ne "python"
    ) {
        return $false
    }
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $descendants = {
        param([int]$RootProcessId)
        $queue = [System.Collections.Generic.Queue[int]]::new()
        $seen = [System.Collections.Generic.HashSet[int]]::new()
        if ($seen.Add($RootProcessId)) { $queue.Enqueue($RootProcessId) }
        while ($queue.Count -gt 0) {
            $parent = $queue.Dequeue()
            foreach ($child in @($all | Where-Object { [int]$_.ParentProcessId -eq $parent })) {
                $childPid = [int]$child.ProcessId
                if ($seen.Add($childPid)) { $queue.Enqueue($childPid) }
            }
        }
        return @($seen)
    }
    if (@(& $descendants $rootPid) -notcontains $ownerPid) {
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
    if (@(& $descendants $ownerPid) -notcontains $listenerPid) {
        return $false
    }
    $listener = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
    $listenerIdentity = $all | Where-Object { [int]$_.ProcessId -eq $listenerPid } | Select-Object -First 1
    if ($null -eq $listener -or $null -eq $listenerIdentity) {
        return $false
    }
    $commandLine = [string]$listenerIdentity.CommandLine
    return (
        (Normalize-ProcessName -Name ([string]$listener.ProcessName)) -eq "python" -and
        $commandLine -match '(?i)apps[\\/]serve_camera_hub\.py'
    )
}

function Test-CameraHubManifestFreshForEntry {
    param(
        [Parameter(Mandatory = $true)][object]$Manifest,
        [object]$Entry
    )
    $updatedAt = [string](Get-ObjectProperty -Object $Manifest -Name "updated_at" -Default "")
    if ([string]::IsNullOrWhiteSpace($updatedAt)) {
        return $false
    }
    try {
        $manifestTime = [DateTimeOffset]::Parse($updatedAt)
        if ($null -eq $Entry) {
            return $false
        }
        $startedAt = [DateTimeOffset]::Parse([string](Get-ObjectProperty -Object $Entry -Name "started_at" -Default ""))
        return $manifestTime -ge $startedAt.AddSeconds(-2)
    }
    catch {
        return $false
    }
}

function Get-CameraHubReadyStatus {
    param(
        [Parameter(Mandatory = $true)][hashtable]$PidState,
        [object]$Entry
    )
    if (-not (Test-CameraHubRegistryEntry -PidState $PidState -Entry $Entry)) {
        return [pscustomobject]@{
            Known = $false
            Valid = $false
            Operational = $false
            Ready = $false
            ListenerValid = $false
            StateClass = "unknown"
            DetailClass = "camera_registry_validation_failed"
        }
    }

    $manifest = Read-JsonFile -Path $CameraHubStateFile
    if ($null -eq $manifest) {
        return [pscustomobject]@{
            Known = $true
            Valid = $false
            Operational = $false
            Ready = $false
            ListenerValid = $false
            StateClass = "unknown"
            DetailClass = "camera_manifest_missing_or_invalid_json"
        }
    }

    $stateClass = [string](Get-ObjectProperty -Object $manifest -Name "camera_state_class" -Default "")
    $readyValue = Get-ObjectProperty -Object $manifest -Name "ready" -Default $null
    $readyAt = [string](Get-ObjectProperty -Object $manifest -Name "ready_at" -Default "")
    $readyAtPresent = -not [string]::IsNullOrWhiteSpace($readyAt)
    $valid = (
        (Get-ObjectProperty -Object $manifest -Name "schema_version" -Default 0) -eq 2 -and
        [string](Get-ObjectProperty -Object $manifest -Name "module" -Default "") -eq "mediapipe-sword-sign" -and
        [string](Get-ObjectProperty -Object $manifest -Name "service" -Default "") -eq "mediapipe_camera_hub_stack" -and
        $stateClass -in @("starting", "unavailable", "recovering", "ready", "stopping") -and
        $readyValue -is [bool] -and
        $readyValue -eq ($stateClass -eq "ready") -and
        $readyValue -eq $readyAtPresent -and
        (Test-CameraHubManifestFreshForEntry -Manifest $manifest -Entry $Entry)
    )
    if ($valid -and $readyAtPresent) {
        try { [void][DateTimeOffset]::Parse($readyAt) }
        catch { $valid = $false }
    }
    $listenerValid = $valid -and (Test-CameraHubRuntimeOwnership -Manifest $manifest -Entry $Entry -Port $MediapipePort)
    if ($valid -and -not $listenerValid) {
        $valid = $false
    }
    $operational = (
        $valid -and $listenerValid -and
        $stateClass -in @("unavailable", "recovering", "ready") -and
        (Test-CameraHubRequiredProcessesRunning -Manifest $manifest)
    )
    return [pscustomobject]@{
        Known = $true
        Valid = $valid
        Operational = $operational
        Ready = ($valid -and $operational -and $stateClass -eq "ready")
        ListenerValid = $listenerValid
        StateClass = if ($valid) { $stateClass } else { "unknown" }
        DetailClass = if ($valid) { "camera_state_$stateClass" } else { "camera_manifest_runtime_or_shape_validation_failed" }
    }
}

function New-StatusRow {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][bool]$ProcessAlive,
        [Parameter(Mandatory = $true)][bool]$PortListening,
        [Parameter(Mandatory = $true)][bool]$HttpOk,
        [Parameter(Mandatory = $true)][string]$Detail,
        [bool]$RequireHttp = $false,
        [string]$StateOverride = ""
    )
    $state = if (-not [string]::IsNullOrWhiteSpace($StateOverride)) {
        $StateOverride
    }
    elseif ($ProcessAlive -and ($PortListening -or $HttpOk)) {
        if ($RequireHttp -and -not $HttpOk) { "DEGRADED" } else { "OK" }
    }
    elseif ($HttpOk) {
        "OK_EXTERNAL"
    }
    elseif ($ProcessAlive) {
        "STARTING/WAITING"
    }
    elseif ($PortListening) {
        "DEGRADED"
    }
    else {
        "DOWN"
    }
    [pscustomobject]@{
        Name = $Name
        State = $state
        Process = if ($ProcessAlive) { "alive" } else { "-" }
        Port = if ($PortListening) { "listen" } else { "-" }
        Http = if ($HttpOk) { "ok" } else { "-" }
        Detail = $Detail
    }
}

function Get-StatusText {
    $pidState = Read-PidState
    if ([string]::IsNullOrWhiteSpace($VoicevoxUrl)) {
        $VoicevoxUrl = Get-DotEnvValue -Path $AituberEnvPath -Name "VOICEVOX_SERVER_URL"
    }
    if ([string]::IsNullOrWhiteSpace($VoicevoxUrl)) {
        $VoicevoxUrl = "http://localhost:50021"
    }
    if ($VoicevoxUrl -match "^http://localhost(?::|/|$)") {
        $VoicevoxUrl = $VoicevoxUrl -replace "^http://localhost", "http://127.0.0.1"
    }
    $VoicevoxUrl = $VoicevoxUrl.TrimEnd("/")

    $rows = @()

    $haEntry = $pidState["home_assistant_bridge"]
    $haHealth = Invoke-JsonHealthCheck -Url "http://127.0.0.1:$HomeAssistantBridgePort/health" -TimeoutSeconds 10
    $rows += New-StatusRow `
        -Name "home_assistant_bridge" `
        -ProcessAlive (Test-ProcessAlive -Entry $haEntry) `
        -PortListening (Test-TcpListen -Port $HomeAssistantBridgePort) `
        -HttpOk $haHealth.Ok `
        -Detail $haHealth.Detail `
        -RequireHttp $true

    $environmentEntry = $pidState["environment_state_server"]
    $environmentHealth = Invoke-JsonHealthCheck -Url "http://127.0.0.1:$EnvironmentStatePort/health"
    $rows += New-StatusRow `
        -Name "environment_state_server" `
        -ProcessAlive (Test-ProcessAlive -Entry $environmentEntry) `
        -PortListening (Test-TcpListen -Port $EnvironmentStatePort) `
        -HttpOk $environmentHealth.Ok `
        -Detail $environmentHealth.Detail `
        -RequireHttp $true

    $mediapipeEntry = $pidState["mediapipe_camera_hub_stack"]
    if ($null -eq $mediapipeEntry) {
        $mediapipeEntry = $pidState["mediapipe_camera_hub"]
    }
    $mediapipeListen = Test-TcpListen -Port $MediapipePort
    $cameraHubReady = Get-CameraHubReadyStatus -PidState $pidState -Entry $pidState["mediapipe_camera_hub_stack"]
    $mediapipeProcessAlive = Test-ProcessAlive -Entry $mediapipeEntry
    $mediapipeState = if (-not $cameraHubReady.Valid) {
        if ($mediapipeProcessAlive -or $mediapipeListen) { "DEGRADED" } else { "DOWN" }
    }
    elseif (-not $mediapipeProcessAlive -or -not $mediapipeListen) {
        "DEGRADED"
    }
    elseif ($cameraHubReady.StateClass -eq "ready" -and $cameraHubReady.Operational) {
        "OK"
    }
    elseif ($cameraHubReady.StateClass -eq "ready") {
        "DEGRADED"
    }
    elseif ($cameraHubReady.StateClass -in @("unavailable", "recovering")) {
        "DEGRADED"
    }
    elseif ($cameraHubReady.StateClass -eq "starting") {
        "STARTING/WAITING"
    }
    else {
        "DOWN"
    }
    $rows += New-StatusRow `
        -Name "mediapipe" `
        -ProcessAlive $mediapipeProcessAlive `
        -PortListening $mediapipeListen `
        -HttpOk $false `
        -Detail $cameraHubReady.DetailClass `
        -StateOverride $mediapipeState

    $visionSnapshotEntry = $pidState["vision_snapshot_processor"]
    $visionSnapshotListen = Test-TcpListen -Port $VisionSnapshotProcessorPort
    if ($null -ne $visionSnapshotEntry -or $visionSnapshotListen) {
        $visionSnapshotDetail = if ($visionSnapshotListen) {
            "ws://127.0.0.1:$VisionSnapshotProcessorPort listening"
        }
        else {
            "waiting for vision snapshot processor"
        }
        $rows += New-StatusRow `
            -Name "vision_snapshot" `
            -ProcessAlive (Test-ProcessAlive -Entry $visionSnapshotEntry) `
            -PortListening $visionSnapshotListen `
            -HttpOk $false `
            -Detail $visionSnapshotDetail
    }

    $aituberEntry = $pidState["aituber_kit"]
    $aituberHealth = Invoke-HttpCheck -Url "http://127.0.0.1:$AituberPort"
    $rows += New-StatusRow `
        -Name "aituber_kit" `
        -ProcessAlive (Test-ProcessAlive -Entry $aituberEntry) `
        -PortListening (Test-TcpListen -Port $AituberPort) `
        -HttpOk $aituberHealth.Ok `
        -Detail $aituberHealth.Detail `
        -RequireHttp $true

    $tdGuiEntry = $pidState["touchdesigner_control_gui"]
    $tdGuiHealth = Invoke-HttpCheck -Url "http://127.0.0.1:$TouchDesignerGuiPort"
    $rows += New-StatusRow `
        -Name "td_control_gui" `
        -ProcessAlive (Test-ProcessAlive -Entry $tdGuiEntry) `
        -PortListening (Test-TcpListen -Port $TouchDesignerGuiPort) `
        -HttpOk $tdGuiHealth.Ok `
        -Detail $tdGuiHealth.Detail `
        -RequireHttp $true

    $thoughtCoreEntry = $pidState["thought_core_api"]
    $thoughtCoreListen = Test-TcpListen -Port $ThoughtCorePort
    $thoughtCoreHealth = Invoke-HttpCheck -Url "http://127.0.0.1:$ThoughtCorePort/health"
    if ($EnableThoughtCore -or $null -ne $thoughtCoreEntry -or $thoughtCoreListen -or $thoughtCoreHealth.Ok) {
        $rows += New-StatusRow `
            -Name "thought_core_api" `
            -ProcessAlive (Test-ProcessAlive -Entry $thoughtCoreEntry) `
            -PortListening $thoughtCoreListen `
            -HttpOk $thoughtCoreHealth.Ok `
            -Detail $thoughtCoreHealth.Detail `
            -RequireHttp $true
    }

    $thoughtCoreWatcherEntry = $pidState["thought_core_watcher"]
    if ($EnableThoughtCoreWatch -or $null -ne $thoughtCoreWatcherEntry) {
        $thoughtCoreWatcherAlive = Test-ProcessAlive -Entry $thoughtCoreWatcherEntry
        $rows += New-StatusRow `
            -Name "thought_core_watcher" `
            -ProcessAlive $thoughtCoreWatcherAlive `
            -PortListening $false `
            -HttpOk $false `
            -Detail "process-only watcher"
    }

    $voicevoxHealth = Invoke-HttpCheck -Url "$VoicevoxUrl/version"
    $voicevoxPort = 0
    try {
        $voicevoxPort = ([System.Uri]$VoicevoxUrl).Port
    }
    catch {
        $voicevoxPort = 50021
    }
    $rows += New-StatusRow `
        -Name "voicevox" `
        -ProcessAlive $false `
        -PortListening (Test-TcpListen -Port $voicevoxPort) `
        -HttpOk $voicevoxHealth.Ok `
        -Detail "$VoicevoxUrl/version :: $($voicevoxHealth.Detail)" `
        -RequireHttp $true

    $table = $rows |
        Format-Table Name, State, Process, Port, Http, Detail -AutoSize |
        Out-String -Width 240

    return @(
        "",
        "Home Control Stack Status  $(Get-Date -Format 'yyyy-MM-dd HH:mm:ss')",
        "PID file: $PidFile",
        $table.TrimEnd()
    ) -join [Environment]::NewLine
}

function Write-StatusFrame {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [Parameter(Mandatory = $true)][int]$Top,
        [int]$PreviousLineCount = 0
    )

    $lines = @($Text -split "`r?`n")
    $width = [Math]::Max(20, [Console]::BufferWidth - 1)
    [Console]::SetCursorPosition(0, $Top)

    foreach ($line in $lines) {
        $rendered = if ($line.Length -gt $width) {
            $line.Substring(0, $width)
        }
        else {
            $line
        }
        [Console]::Out.WriteLine($rendered.PadRight($width))
    }

    for ($i = $lines.Count; $i -lt $PreviousLineCount; $i++) {
        [Console]::Out.WriteLine(("").PadRight($width))
    }

    return $lines.Count
}

if ($Watch) {
    $top = [Console]::CursorTop
    $previousLineCount = 0
    $oldCursorVisible = [Console]::CursorVisible
    try {
        [Console]::CursorVisible = $false
        while ($true) {
            $previousLineCount = Write-StatusFrame `
                -Text (Get-StatusText) `
                -Top $top `
                -PreviousLineCount $previousLineCount
            Start-Sleep -Seconds $IntervalSeconds
        }
    }
    finally {
        [Console]::CursorVisible = $oldCursorVisible
    }
}
else {
    Write-Host (Get-StatusText)
}
