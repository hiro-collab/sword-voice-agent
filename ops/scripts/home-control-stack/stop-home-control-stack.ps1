param(
    [string]$WorkspaceRoot = "",
    [string]$StackStateDir = "",
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "..\..\..\scripts\common.ps1")

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

$StopSettleTimeoutMilliseconds = 10000
if ($env:NODE_ENV -eq "test") {
    $testSettleTimeout = 0
    if (
        [int]::TryParse(
            [string]$env:HOME_CONTROL_STACK_STOP_TEST_SETTLE_TIMEOUT_MS,
            [ref]$testSettleTimeout
        ) -and
        $testSettleTimeout -ge 50 -and
        $testSettleTimeout -le 30000
    ) {
        $StopSettleTimeoutMilliseconds = $testSettleTimeout
    }
}

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

function Test-ExactIntegerValue {
    param(
        [object]$Value,
        [long]$ExpectedValue
    )
    return (
        $Value -is [byte] -or $Value -is [sbyte] -or
        $Value -is [short] -or $Value -is [ushort] -or
        $Value -is [int] -or $Value -is [uint] -or
        $Value -is [long] -or $Value -is [ulong]
    ) -and $Value -eq $ExpectedValue
}

function Test-LegacyV2DefaultOwnershipBundle {
    param([Parameter(Mandatory = $true)][object]$Entry)

    $requiredFields = @(
        "ownership_class",
        "ownership_root_pid",
        "ownership_root_started_at",
        "ownership_parent_pid",
        "ownership_lineage",
        "ownership_seal",
        "expected_module",
        "expected_port"
    )
    foreach ($field in $requiredFields) {
        if ($null -eq $Entry.PSObject.Properties[$field]) {
            return $false
        }
    }
    foreach ($field in @(
        "ownership_class",
        "ownership_root_started_at",
        "ownership_seal",
        "expected_module"
    )) {
        $value = $Entry.PSObject.Properties[$field].Value
        if ($value -isnot [string] -or $value.Length -ne 0) {
            return $false
        }
    }
    foreach ($field in @("ownership_root_pid", "ownership_parent_pid", "expected_port")) {
        if (-not (Test-ExactIntegerValue -Value $Entry.PSObject.Properties[$field].Value -ExpectedValue 0)) {
            return $false
        }
    }
    $lineage = $Entry.PSObject.Properties["ownership_lineage"].Value
    if ($lineage -isnot [Array] -or $lineage.Count -ne 0) {
        return $false
    }
    $stopStrategyProperty = $Entry.PSObject.Properties["stop_strategy"]
    if ($null -eq $stopStrategyProperty) {
        return $false
    }
    $stopStrategy = $stopStrategyProperty.Value
    return $stopStrategy -is [string] -and $stopStrategy -ceq "managed_tree"
}

function Test-SealedDescendantListenerRecordShape {
    param(
        [Parameter(Mandatory = $true)][object]$Entry,
        [int]$RegistrySchemaVersion = 0
    )

    if (
        $RegistrySchemaVersion -eq 2 -and
        (Test-LegacyV2DefaultOwnershipBundle -Entry $Entry)
    ) {
        return $false
    }

    foreach ($field in @(
        "ownership_seal",
        "ownership_root_pid",
        "ownership_root_started_at",
        "ownership_parent_pid",
        "ownership_lineage",
        "expected_module",
        "expected_port"
    )) {
        if ($null -ne $Entry.PSObject.Properties[$field]) {
            return $true
        }
    }
    $stopStrategy = [string](Get-ObjectProperty -Object $Entry -Name "stop_strategy" -Default "")
    return $stopStrategy -ceq "role_scoped_descendant"
}

function Get-ManagedTargetPids {
    param(
        [Parameter(Mandatory = $true)][object]$Entry,
        [int]$RegistrySchemaVersion = 0
    )
    $ownershipClass = [string](Get-ObjectProperty -Object $Entry -Name "ownership_class" -Default "")
    $sealedRecordShape = Test-SealedDescendantListenerRecordShape `
        -Entry $Entry `
        -RegistrySchemaVersion $RegistrySchemaVersion
    $sealedClassSpec = if ([string]::IsNullOrWhiteSpace($ownershipClass)) { $null } else {
        Get-SwordSealedListenerClassSpec -OwnershipClass $ownershipClass
    }
    if ($null -ne $sealedClassSpec) {
        $inspectionFailure = (
            $env:NODE_ENV -eq "test" -and
            (
                $env:HOME_CONTROL_STACK_STOP_TEST_VSP_INSPECTION_FAILURE -eq "true" -or
                $env:HOME_CONTROL_STACK_STOP_TEST_REVALIDATE_FAILURE -eq "true"
            )
        )
        $validation = Test-SwordSealedDescendantListenerEntry `
            -Entry $Entry `
            -RequiredOwnershipClass $ownershipClass `
            -SimulateInspectionFailure:$inspectionFailure
        if ($validation.Valid) {
            return @([int](Get-ObjectProperty -Object $Entry -Name "pid" -Default 0))
        }
        Write-Host "Sealed descendant listener ownership unavailable: $($validation.Reason)"
        return @()
    }
    if ($sealedRecordShape) {
        Write-Host "Sealed descendant listener ownership unavailable: class_invalid"
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
    param(
        [Parameter(Mandatory = $true)][object]$Entry,
        [int]$RegistrySchemaVersion = 0
    )

    $name = [string](Get-ObjectProperty -Object $Entry -Name "name" -Default "unknown")
    $module = [string](Get-ObjectProperty -Object $Entry -Name "module" -Default "")
    $role = [string](Get-ObjectProperty -Object $Entry -Name "role" -Default "")
    $allowedNames = @(ConvertTo-StringArray -Value (Get-ObjectProperty -Object $Entry -Name "allowed_process_names" -Default @()))
    $targetIds = @(Get-ManagedTargetPids -Entry $Entry -RegistrySchemaVersion $RegistrySchemaVersion)
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
        $sealedClassSpec = if ([string]::IsNullOrWhiteSpace($ownershipClass)) { $null } else {
            Get-SwordSealedListenerClassSpec -OwnershipClass $ownershipClass
        }
        if ($null -ne $sealedClassSpec) {
            $inspectionFailure = (
                $env:NODE_ENV -eq "test" -and
                (
                    $env:HOME_CONTROL_STACK_STOP_TEST_VSP_INSPECTION_FAILURE -eq "true" -or
                    $env:HOME_CONTROL_STACK_STOP_TEST_REVALIDATE_FAILURE -eq "true"
                )
            )
            $validation = Test-SwordSealedDescendantListenerEntry `
                -Entry $Entry `
                -RequiredOwnershipClass $ownershipClass `
                -SimulateInspectionFailure:$inspectionFailure
            if (-not $validation.Valid -or [int]$validation.ListenerPid -ne $pidValue) {
                Write-Host "skipped PID $pidValue (sealed listener identity changed before termination: $($validation.Reason))"
                continue
            }
            if ($DryRun) {
                Write-Host "[dry-run] stop PID $pidValue python [sealed_descendant_listener]"
                continue
            }
            try {
                Stop-Process -Id $pidValue -Force -ErrorAction Stop
                Write-Host "stopped PID $pidValue python [sealed_descendant_listener]"
            }
            catch {
                Write-Warning "failed to stop sealed descendant listener PID $pidValue"
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
$registrySchemaVersion = 0
if ($null -ne $state) {
    $schemaValue = Get-ObjectProperty -Object $state -Name "schema_version" -Default $null
    if (Test-ExactIntegerValue -Value $schemaValue -ExpectedValue 2) {
        $registrySchemaVersion = 2
    }
}
$managedEntries = @()
$preStopTargetIds = @()
if ($null -ne $state -and $null -ne $state.processes) {
    foreach ($entry in $state.processes) {
        $targets = @(Get-ManagedTargetPids -Entry $entry -RegistrySchemaVersion $registrySchemaVersion)
        if ($targets.Count -gt 0) {
            $managedEntries += $entry
            $preStopTargetIds += $targets
        }
        else {
            $entryOwnershipClass = [string](Get-ObjectProperty -Object $entry -Name "ownership_class" -Default "")
            $entrySealedRecordShape = Test-SealedDescendantListenerRecordShape `
                -Entry $entry `
                -RegistrySchemaVersion $registrySchemaVersion
            $entrySealedClassSpec = if ([string]::IsNullOrWhiteSpace($entryOwnershipClass)) { $null } else {
                Get-SwordSealedListenerClassSpec -OwnershipClass $entryOwnershipClass
            }
            if ($null -eq $entrySealedClassSpec -and -not $entrySealedRecordShape) { continue }
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
        Stop-ManagedProcessEntry `
            -Entry $managedEntries[$index] `
            -RegistrySchemaVersion $registrySchemaVersion
    }
}

if (-not $DryRun) {
    $remainingTargetIds = @()
    $settleDeadline = [DateTimeOffset]::UtcNow.AddMilliseconds($StopSettleTimeoutMilliseconds)
    do {
        $remainingTargetIds = @(
            $preStopTargetIds |
                Sort-Object -Unique |
                Where-Object { $null -ne (Get-Process -Id $_ -ErrorAction SilentlyContinue) }
        )
        if ($remainingTargetIds.Count -eq 0) {
            break
        }
        Start-Sleep -Milliseconds 100
    } while ([DateTimeOffset]::UtcNow -lt $settleDeadline)
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
