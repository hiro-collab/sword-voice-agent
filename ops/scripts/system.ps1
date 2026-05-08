param(
    [Parameter(Position = 0)]
    [ValidateSet("status", "start", "stop")]
    [string]$Command = "status",
    [string]$Profile = "thought-core-experimental",
    [string]$WorkspaceRoot = "",
    [string]$StackStateDir = ""
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

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
    if ($null -eq $property) {
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
        $null = Get-Process -Id $pidValue -ErrorAction Stop
        return $true
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

$repoRoot = Resolve-RepoRoot
$workspaceRoot = Resolve-WorkspaceRoot -Value $WorkspaceRoot
$stackStateDir = Resolve-StackStateDir -WorkspaceRoot $workspaceRoot -Value $StackStateDir
$manifestRoot = Join-Path $repoRoot "ops\manifests"
$profilePath = Join-Path $manifestRoot "profiles\$Profile.json"

if ($Command -ne "status") {
    Write-Error "ops/scripts/system.ps1 currently supports read-only status only. Use scripts/home-control-stack for $Command."
}

$profileManifest = Read-JsonObject -Path $profilePath
$pidFile = Join-Path $stackStateDir "pids.json"
$pidMap = Read-PidMap -Path $pidFile
$services = ConvertTo-StringArray -Value (Get-ObjectProperty -Object $profileManifest -Name "services" -Default @())

Write-Host ("[ops] command=status profile={0} state_dir={1}" -f $Profile, $stackStateDir)
foreach ($serviceId in $services) {
    $servicePath = Join-Path $manifestRoot "services\$serviceId.json"
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
