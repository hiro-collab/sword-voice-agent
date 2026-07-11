param(
    [string]$WorkspaceRoot = "",
    [string]$StackStateDir = "",
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

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

$StateDir = $StackStateDir
$PidFile = Join-Path $StateDir "pids.json"

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

function Read-PidState {
    if (-not (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $PidFile -Raw -Encoding UTF8 | ConvertFrom-Json
    }
    catch {
        Write-Warning "Failed to read PID file: $PidFile"
        return $null
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
    if ($null -eq $property) {
        return $Default
    }
    if ($null -eq $property.Value) {
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

function Test-ProcessStartTimeMatches {
    param(
        [Parameter(Mandatory = $true)][object]$Process,
        [string]$RecordedAt,
        [int]$StartAfterRecordedToleranceSeconds = 2
    )
    if ([string]::IsNullOrWhiteSpace($RecordedAt)) {
        return $false
    }
    try {
        $recorded = [DateTimeOffset]::Parse($RecordedAt)
        $processStarted = [DateTimeOffset]$Process.StartTime
        return (
            $processStarted -ge $recorded.AddSeconds(-10) -and
            $processStarted -le $recorded.AddSeconds($StartAfterRecordedToleranceSeconds)
        )
    }
    catch {
        return $false
    }
}

function Read-ChildProcessIds {
    param([string]$Path)
    if ([string]::IsNullOrWhiteSpace($Path) -or -not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return @()
    }
    try {
        $raw = Get-Content -LiteralPath $Path -Raw -Encoding UTF8 | ConvertFrom-Json
        $pids = @()
        foreach ($entry in @($raw.processes)) {
            $pidValue = [int](Get-ObjectProperty -Object $entry -Name "pid" -Default 0)
            if ($pidValue -gt 0) {
                $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
                if ($null -eq $process) {
                    continue
                }
                $startedAt = [string](Get-ObjectProperty -Object $entry -Name "started_at" -Default "")
                if (-not (Test-ProcessStartTimeMatches -Process $process -RecordedAt $startedAt)) {
                    Write-Host "skipped PID $pidValue $($process.ProcessName) (stale child process manifest entry)"
                    continue
                }
                $pids += $pidValue
            }
        }
        return @($pids)
    }
    catch {
        Write-Warning "failed to read child process file: $Path"
        return @()
    }
}

function Test-VisionSnapshotListenerCommand {
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

function ConvertTo-OwnershipTimestamp {
    param([object]$Value)
    if ($Value -is [DateTimeOffset]) {
        return ([DateTimeOffset]$Value).ToString("o")
    }
    if ($Value -is [DateTime]) {
        return ([DateTime]$Value).ToString("o")
    }
    return [string]$Value
}

function Get-VisionSnapshotOwnershipSeal {
    param([Parameter(Mandatory = $true)][object]$Entry)
    $parts = @(
        "vsp_descendant_listener.v0"
        [string][int](Get-ObjectProperty -Object $Entry -Name "pid" -Default 0)
        ConvertTo-OwnershipTimestamp (Get-ObjectProperty -Object $Entry -Name "started_at" -Default "")
        [string][int](Get-ObjectProperty -Object $Entry -Name "ownership_parent_pid" -Default 0)
        [string][int](Get-ObjectProperty -Object $Entry -Name "ownership_root_pid" -Default 0)
        ConvertTo-OwnershipTimestamp (Get-ObjectProperty -Object $Entry -Name "ownership_root_started_at" -Default "")
        [string](Get-ObjectProperty -Object $Entry -Name "expected_module" -Default "")
        [string][int](Get-ObjectProperty -Object $Entry -Name "expected_port" -Default 0)
    )
    foreach ($row in @((Get-ObjectProperty -Object $Entry -Name "ownership_lineage" -Default @()))) {
        $parts += (
            "{0}|{1}|{2}|{3}" -f
            [int](Get-ObjectProperty -Object $row -Name "pid" -Default 0),
            [int](Get-ObjectProperty -Object $row -Name "parent_pid" -Default 0),
            [string](Get-ObjectProperty -Object $row -Name "process_name" -Default ""),
            (ConvertTo-OwnershipTimestamp (Get-ObjectProperty -Object $row -Name "started_at" -Default ""))
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

function Test-VisionSnapshotListenerEntryOwned {
    param([Parameter(Mandatory = $true)][object]$Entry)

    if ($env:NODE_ENV -eq "test" -and $env:HOME_CONTROL_STACK_STOP_TEST_VSP_INSPECTION_FAILURE -eq "true") {
        Write-Host "VSP listener ownership unavailable: inspection_failed"
        return $false
    }
    if ([string](Get-ObjectProperty -Object $Entry -Name "ownership_class" -Default "") -ne "vsp_descendant_listener.v0") {
        Write-Host "VSP listener ownership unavailable: class_invalid"
        return $false
    }
    $storedSeal = [string](Get-ObjectProperty -Object $Entry -Name "ownership_seal" -Default "")
    if (
        $storedSeal -notmatch '^[0-9a-f]{64}$' -or
        (Get-VisionSnapshotOwnershipSeal -Entry $Entry) -cne $storedSeal
    ) {
        Write-Host "VSP listener ownership unavailable: seal_invalid"
        return $false
    }
    if (
        [string](Get-ObjectProperty -Object $Entry -Name "role" -Default "") -ne "vision_snapshot_processor_listener" -or
        [string](Get-ObjectProperty -Object $Entry -Name "expected_module" -Default "") -ne "vision_snapshot_processor.main"
    ) {
        Write-Host "VSP listener ownership unavailable: role_invalid"
        return $false
    }
    $listenerPid = [int](Get-ObjectProperty -Object $Entry -Name "pid" -Default 0)
    $parentPid = [int](Get-ObjectProperty -Object $Entry -Name "ownership_parent_pid" -Default 0)
    $rootPid = [int](Get-ObjectProperty -Object $Entry -Name "ownership_root_pid" -Default 0)
    $port = [int](Get-ObjectProperty -Object $Entry -Name "expected_port" -Default 0)
    if ($listenerPid -le 0 -or $parentPid -le 0 -or $rootPid -le 0 -or $port -le 0) {
        Write-Host "VSP listener ownership unavailable: correlation_invalid"
        return $false
    }
    $listener = Get-Process -Id $listenerPid -ErrorAction SilentlyContinue
    $identity = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid" -ErrorAction SilentlyContinue
    if (
        $null -eq $listener -or $null -eq $identity -or
        -not (Test-ProcessNameAllowed -ProcessName ([string]$listener.ProcessName) -AllowedProcessNames @("python")) -or
        -not (Test-ProcessStartTimeMatches -Process $listener -RecordedAt ([string](Get-ObjectProperty -Object $Entry -Name "started_at" -Default ""))) -or
        [int]$identity.ParentProcessId -ne $parentPid -or
        -not (Test-VisionSnapshotListenerCommand -CommandLine ([string]$identity.CommandLine) -Port $port)
    ) {
        Write-Host "VSP listener ownership unavailable: listener_identity_invalid"
        return $false
    }
    $portOwners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction SilentlyContinue | Select-Object -ExpandProperty OwningProcess -Unique)
    if ($portOwners.Count -ne 1 -or [int]$portOwners[0] -ne $listenerPid) {
        Write-Host "VSP listener ownership unavailable: port_owner_invalid"
        return $false
    }

    $rootStartedAt = ConvertTo-OwnershipTimestamp (Get-ObjectProperty -Object $Entry -Name "ownership_root_started_at" -Default "")
    if ([string]::IsNullOrWhiteSpace($rootStartedAt)) {
        Write-Host "VSP listener ownership unavailable: root_time_invalid"
        return $false
    }
    $lineage = @((Get-ObjectProperty -Object $Entry -Name "ownership_lineage" -Default @()))
    if (
        $lineage.Count -eq 0 -or $lineage.Count -gt 8 -or
        [int](Get-ObjectProperty -Object $lineage[0] -Name "pid" -Default 0) -ne $parentPid -or
        [int](Get-ObjectProperty -Object $lineage[-1] -Name "pid" -Default 0) -ne $rootPid -or
        (ConvertTo-OwnershipTimestamp (Get-ObjectProperty -Object $lineage[-1] -Name "started_at" -Default "")) -ne $rootStartedAt
    ) {
        Write-Host "VSP listener ownership unavailable: lineage_invalid"
        return $false
    }
    for ($index = 0; $index -lt $lineage.Count; $index++) {
        $ancestorRecord = $lineage[$index]
        $ancestorPid = [int](Get-ObjectProperty -Object $ancestorRecord -Name "pid" -Default 0)
        $ancestorParentPid = [int](Get-ObjectProperty -Object $ancestorRecord -Name "parent_pid" -Default 0)
        $ancestorName = Normalize-ProcessName -Name ([string](Get-ObjectProperty -Object $ancestorRecord -Name "process_name" -Default ""))
        $ancestorStartedAt = ConvertTo-OwnershipTimestamp (Get-ObjectProperty -Object $ancestorRecord -Name "started_at" -Default "")
        if (
            $ancestorPid -le 0 -or $ancestorParentPid -le 0 -or
            [string]::IsNullOrWhiteSpace($ancestorName) -or
            [string]::IsNullOrWhiteSpace($ancestorStartedAt) -or
            $ExternalProcessDenyList -contains $ancestorName
        ) {
            Write-Host "VSP listener ownership unavailable: lineage_invalid"
            return $false
        }
        if ($index + 1 -lt $lineage.Count) {
            $nextPid = [int](Get-ObjectProperty -Object $lineage[$index + 1] -Name "pid" -Default 0)
            if ($ancestorParentPid -ne $nextPid) {
                Write-Host "VSP listener ownership unavailable: lineage_invalid"
                return $false
            }
        }
        $ancestor = Get-Process -Id $ancestorPid -ErrorAction SilentlyContinue
        $ancestorIdentity = Get-CimInstance Win32_Process -Filter "ProcessId = $ancestorPid" -ErrorAction SilentlyContinue
        if ($null -ne $ancestor -or $null -ne $ancestorIdentity) {
            if (
                $null -eq $ancestor -or $null -eq $ancestorIdentity -or
                (Normalize-ProcessName -Name ([string]$ancestor.ProcessName)) -ne $ancestorName -or
                -not (Test-ProcessStartTimeMatches -Process $ancestor -RecordedAt $ancestorStartedAt) -or
                [int]$ancestorIdentity.ParentProcessId -ne $ancestorParentPid
            ) {
                Write-Host "VSP listener ownership unavailable: lineage_changed"
                return $false
            }
        }
    }
    return $true
}

function Get-ManagedTargetPids {
    param([Parameter(Mandatory = $true)][object]$Entry)
    if ([string](Get-ObjectProperty -Object $Entry -Name "ownership_class" -Default "") -eq "vsp_descendant_listener.v0") {
        if (Test-VisionSnapshotListenerEntryOwned -Entry $Entry) {
            return @([int](Get-ObjectProperty -Object $Entry -Name "pid" -Default 0))
        }
        return @()
    }
    $targetIds = @()
    $rootPid = [int](Get-ObjectProperty -Object $Entry -Name "pid" -Default 0)
    $rootProcess = if ($rootPid -gt 0) { Get-Process -Id $rootPid -ErrorAction SilentlyContinue } else { $null }
    if ($null -eq $rootProcess) {
        return @()
    }
    $allowedNames = @(ConvertTo-StringArray -Value (Get-ObjectProperty -Object $Entry -Name "allowed_process_names" -Default @()))
    $startedAt = [string](Get-ObjectProperty -Object $Entry -Name "started_at" -Default "")
    if (
        (Test-ProcessNameAllowed -ProcessName ([string]$rootProcess.ProcessName) -AllowedProcessNames $allowedNames) -and
        (Test-ProcessStartTimeMatches -Process $rootProcess -RecordedAt $startedAt)
    ) {
        $targetIds += Get-DescendantProcessIds -RootProcessId $rootPid
    }
    else {
        Write-Host "skipped root PID $rootPid $($rootProcess.ProcessName) (not an owned registry root)"
        return @()
    }
    $childProcessFile = [string](Get-ObjectProperty -Object $Entry -Name "child_process_file" -Default "")
    $targetIds += Read-ChildProcessIds -Path $childProcessFile
    return @($targetIds | Sort-Object -Unique -Descending)
}

function Test-ProcessNameAllowed {
    param(
        [Parameter(Mandatory = $true)][string]$ProcessName,
        [string[]]$AllowedProcessNames = @()
    )
    $normalizedName = Normalize-ProcessName -Name $ProcessName
    if ($ExternalProcessDenyList -contains $normalizedName) {
        return $false
    }
    if ($AllowedProcessNames.Count -eq 0) {
        return $false
    }
    $allowed = @($AllowedProcessNames | ForEach-Object { Normalize-ProcessName -Name $_ })
    return $allowed -contains $normalizedName
}

function Stop-ManagedProcessEntry {
    param([Parameter(Mandatory = $true)][object]$Entry)

    $name = [string](Get-ObjectProperty -Object $Entry -Name "name" -Default "unknown")
    $module = [string](Get-ObjectProperty -Object $Entry -Name "module" -Default "")
    $role = [string](Get-ObjectProperty -Object $Entry -Name "role" -Default "")
    $allowedNames = @(ConvertTo-StringArray -Value (Get-ObjectProperty -Object $Entry -Name "allowed_process_names" -Default @()))
    $targetIds = @(Get-ManagedTargetPids -Entry $Entry)
    if ($targetIds.Count -eq 0) {
        return
    }

    $label = $name
    if (-not [string]::IsNullOrWhiteSpace($module) -or -not [string]::IsNullOrWhiteSpace($role)) {
        $label = "$name ($module/$role)"
    }
    Write-Host "Target managed process: $label :: PIDs $($targetIds -join ', ')"

    foreach ($pidValue in $targetIds) {
        if ($pidValue -eq $PID) {
            continue
        }
        $ownershipClass = [string](Get-ObjectProperty -Object $Entry -Name "ownership_class" -Default "")
        if ($ownershipClass -eq "vsp_descendant_listener.v0") {
            if (-not (Test-VisionSnapshotListenerEntryOwned -Entry $Entry)) {
                Write-Host "skipped PID $pidValue (VSP listener identity changed before termination)"
                continue
            }
            if ($DryRun) {
                Write-Host "[dry-run] stop PID $pidValue python [vision_snapshot_processor_listener]"
                continue
            }
            try {
                Stop-Process -Id $pidValue -Force -ErrorAction Stop
                Write-Host "stopped PID $pidValue python [vision_snapshot_processor_listener]"
            }
            catch {
                Write-Warning "failed to stop VSP listener PID $pidValue"
            }
            continue
        }
        $rootPid = [int](Get-ObjectProperty -Object $Entry -Name "pid" -Default 0)
        $rootProcess = if ($rootPid -gt 0) { Get-Process -Id $rootPid -ErrorAction SilentlyContinue } else { $null }
        $startedAt = [string](Get-ObjectProperty -Object $Entry -Name "started_at" -Default "")
        $testRevalidationFailure = (
            $env:NODE_ENV -eq "test" -and
            $env:HOME_CONTROL_STACK_STOP_TEST_REVALIDATE_FAILURE -eq "true"
        )
        if (
            $testRevalidationFailure -or
            $null -eq $rootProcess -or
            -not (Test-ProcessNameAllowed -ProcessName ([string]$rootProcess.ProcessName) -AllowedProcessNames $allowedNames) -or
            -not (Test-ProcessStartTimeMatches -Process $rootProcess -RecordedAt $startedAt)
        ) {
            Write-Host "skipped PID $pidValue (registry root identity changed before termination)"
            continue
        }
        $currentTargets = @(Get-DescendantProcessIds -RootProcessId $rootPid)
        if (($pidValue -ne $rootPid) -and ($currentTargets -notcontains $pidValue)) {
            Write-Host "skipped PID $pidValue (no longer a current registry-root descendant)"
            continue
        }
        $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            continue
        }
        $processName = [string]$process.ProcessName
        $normalizedName = Normalize-ProcessName -Name $processName
        if ($ExternalProcessDenyList -contains $normalizedName) {
            Write-Host "skipped PID $pidValue $processName (external browser/updater process)"
            continue
        }
        if (-not (Test-ProcessNameAllowed -ProcessName $processName -AllowedProcessNames $allowedNames)) {
            Write-Host "skipped PID $pidValue $processName (not owned by $name allowed_process_names)"
            continue
        }
        if ($DryRun) {
            Write-Host "[dry-run] stop PID $pidValue $processName [$name]"
            continue
        }
        try {
            Stop-Process -Id $pidValue -Force -ErrorAction Stop
            Write-Host "stopped PID $pidValue $processName [$name]"
        }
        catch {
            Write-Warning "failed to stop PID $pidValue ${processName}: $($_.Exception.Message)"
        }
    }
}

$state = Read-PidState
$managedEntries = @()
$preStopTargetIds = @()
if ($null -ne $state -and $null -ne $state.processes) {
    foreach ($entry in $state.processes) {
        $targets = @(Get-ManagedTargetPids -Entry $entry)
        if ($targets.Count -gt 0) {
            $managedEntries += $entry
            $preStopTargetIds += $targets
        }
        elseif (
            [string](Get-ObjectProperty -Object $entry -Name "ownership_class" -Default "") -eq "vsp_descendant_listener.v0"
        ) {
            $unverifiedPid = [int](Get-ObjectProperty -Object $entry -Name "pid" -Default 0)
            if ($unverifiedPid -gt 0 -and $null -ne (Get-Process -Id $unverifiedPid -ErrorAction SilentlyContinue)) {
                $preStopTargetIds += $unverifiedPid
            }
        }
    }
}

if ($managedEntries.Count -eq 0) {
    Write-Host "No recorded home-control stack processes are running."
}
else {
    Write-Host "Target managed services: $((@($managedEntries | ForEach-Object { [string](Get-ObjectProperty -Object $_ -Name 'name' -Default 'unknown') })) -join ', ')"
    if (-not $Force -and -not $DryRun) {
        $answer = Read-Host "Stop these managed processes? [y/N]"
        if ($answer -notmatch "^(y|yes)$") {
            Write-Host "Canceled."
            return
        }
    }
    for ($index = $managedEntries.Count - 1; $index -ge 0; $index--) {
        Stop-ManagedProcessEntry -Entry $managedEntries[$index]
    }
}

if (-not $DryRun) {
    $remainingTargetIds = @(
        $preStopTargetIds |
            Sort-Object -Unique |
            Where-Object { $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue) }
    )
    if ($remainingTargetIds.Count -gt 0) {
        throw "Managed process stop incomplete; retained PID registry for verification: $($remainingTargetIds -join ', ')"
    }
}

if (-not $DryRun -and (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}
