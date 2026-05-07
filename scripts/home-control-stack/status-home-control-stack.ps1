param(
    [string]$WorkspaceRoot = "",
    [int]$HomeAssistantBridgePort = 8787,
    [int]$EnvironmentStatePort = 8790,
    [int]$MediapipePort = 8765,
    [int]$VisionSnapshotProcessorPort = 8776,
    [int]$AituberPort = 3000,
    [int]$TouchDesignerGuiPort = 8788,
    [int]$DifyPort = 8080,
    [string]$VoicevoxUrl = "",
    [switch]$Watch,
    [int]$IntervalSeconds = 3
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

. (Join-Path $PSScriptRoot "resolve-home-control-workspace.ps1")
$WorkspaceRoot = Resolve-HomeControlWorkspaceRoot -WorkspaceRoot $WorkspaceRoot -ScriptRoot $PSScriptRoot
$PidFile = Join-Path $WorkspaceRoot ".cache\home-control-stack\pids.json"
$AituberEnvPath = Join-Path $WorkspaceRoot "aituber-kit\.env"

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
        $map = @{}
        foreach ($entry in @($raw.processes)) {
            $map[[string]$entry.name] = $entry
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

function Get-CameraHubReadyStatus {
    param([object]$Entry)
    $path = [string](Get-ObjectProperty -Object $Entry -Name "child_process_file" -Default "")
    if ([string]::IsNullOrWhiteSpace($path)) {
        return [pscustomobject]@{
            Known = $false
            Ready = $false
            Detail = ""
        }
    }

    $manifest = Read-JsonFile -Path $path
    if ($null -eq $manifest) {
        return [pscustomobject]@{
            Known = $true
            Ready = $false
            Detail = "waiting for Camera Hub process manifest"
        }
    }

    $detail = [string]$manifest.ready_detail
    if ([string]::IsNullOrWhiteSpace($detail)) {
        $detail = if ($manifest.ready -eq $true) { "Camera Hub topics ready" } else { "waiting for Camera Hub topics" }
    }
    return [pscustomobject]@{
        Known = $true
        Ready = ($manifest.ready -eq $true)
        Detail = $detail
    }
}

function New-StatusRow {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][bool]$ProcessAlive,
        [Parameter(Mandatory = $true)][bool]$PortListening,
        [Parameter(Mandatory = $true)][bool]$HttpOk,
        [Parameter(Mandatory = $true)][string]$Detail,
        [bool]$RequireHttp = $false
    )
    $state = if ($ProcessAlive -and ($PortListening -or $HttpOk)) {
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

    $mediapipeEntry = $pidState["mediapipe_camera_hub"]
    if ($null -eq $mediapipeEntry) {
        $mediapipeEntry = $pidState["mediapipe_camera_hub_stack"]
    }
    if ($null -eq $mediapipeEntry) {
        $mediapipeEntry = $pidState["mediapipe_ws"]
    }
    $mediapipeListen = Test-TcpListen -Port $MediapipePort
    $cameraHubReady = Get-CameraHubReadyStatus -Entry $mediapipeEntry
    $mediapipeReady = if ($cameraHubReady.Known) { $cameraHubReady.Ready } else { $mediapipeListen }
    $mediapipeDetail = if ($mediapipeReady -and $mediapipeListen) {
        if ($cameraHubReady.Known) { $cameraHubReady.Detail } else { "ws://127.0.0.1:$MediapipePort listening" }
    }
    elseif ($mediapipeListen) {
        "ws://127.0.0.1:$MediapipePort listening; $($cameraHubReady.Detail)"
    }
    else {
        "waiting for Camera Hub WebSocket"
    }
    $rows += New-StatusRow `
        -Name "mediapipe" `
        -ProcessAlive (Test-ProcessAlive -Entry $mediapipeEntry) `
        -PortListening ($mediapipeListen -and $mediapipeReady) `
        -HttpOk $false `
        -Detail $mediapipeDetail

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

    $difyHealth = Invoke-HttpCheck -Url "http://127.0.0.1:$DifyPort"
    $rows += New-StatusRow `
        -Name "dify" `
        -ProcessAlive $false `
        -PortListening (Test-TcpListen -Port $DifyPort) `
        -HttpOk $difyHealth.Ok `
        -Detail $difyHealth.Detail `
        -RequireHttp $true

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
