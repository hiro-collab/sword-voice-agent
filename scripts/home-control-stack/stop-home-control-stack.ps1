param(
    [string]$WorkspaceRoot = "",
    [string]$DifyDockerRoot = "",
    [switch]$StopDify,
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "resolve-home-control-workspace.ps1")
$WorkspaceRoot = Resolve-HomeControlWorkspaceRoot -WorkspaceRoot $WorkspaceRoot -ScriptRoot $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($DifyDockerRoot)) {
    $DifyDockerRoot = [Environment]::GetEnvironmentVariable("DIFY_DOCKER_ROOT")
}

$StateDir = Join-Path $WorkspaceRoot ".cache\home-control-stack"
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
                if (-not (Test-ProcessStartTimeMatches -Process $process -RecordedAt $startedAt -GraceSeconds 60)) {
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

function Get-ManagedTargetPids {
    param([Parameter(Mandatory = $true)][object]$Entry)
    $targetIds = @()
    $rootPid = [int](Get-ObjectProperty -Object $Entry -Name "pid" -Default 0)
    $rootProcess = if ($rootPid -gt 0) { Get-Process -Id $rootPid -ErrorAction SilentlyContinue } else { $null }
    if ($null -ne $rootProcess) {
        $allowedNames = @(ConvertTo-StringArray -Value (Get-ObjectProperty -Object $Entry -Name "allowed_process_names" -Default @()))
        $startedAt = [string](Get-ObjectProperty -Object $Entry -Name "started_at" -Default "")
        if (
            (Test-ProcessNameAllowed -ProcessName ([string]$rootProcess.ProcessName) -AllowedProcessNames $allowedNames) -and
            (Test-ProcessStartTimeMatches -Process $rootProcess -RecordedAt $startedAt -GraceSeconds 60)
        ) {
            $targetIds += Get-DescendantProcessIds -RootProcessId $rootPid
        }
        else {
            Write-Host "skipped root PID $rootPid $($rootProcess.ProcessName) (not an owned registry root)"
        }
    }
    $childProcessFile = [string](Get-ObjectProperty -Object $Entry -Name "child_process_file" -Default "")
    $targetIds += Read-ChildProcessIds -Path $childProcessFile
    return @($targetIds | Sort-Object -Unique -Descending)
}

function Test-ProcessNameAllowed {
    param(
        [Parameter(Mandatory = $true)][string]$ProcessName,
        [Parameter(Mandatory = $true)][string[]]$AllowedProcessNames
    )
    $normalizedName = Normalize-ProcessName -Name $ProcessName
    if ($ExternalProcessDenyList -contains $normalizedName) {
        return $false
    }
    if ($AllowedProcessNames.Count -eq 0) {
        return $true
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
if ($null -ne $state -and $null -ne $state.processes) {
    foreach ($entry in $state.processes) {
        $targets = @(Get-ManagedTargetPids -Entry $entry)
        if ($targets.Count -gt 0) {
            $managedEntries += $entry
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

if (-not $DryRun -and (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
    Remove-Item -LiteralPath $PidFile -Force
}

if ($StopDify) {
    if ([string]::IsNullOrWhiteSpace($DifyDockerRoot)) {
        Write-Warning "Dify docker directory is not configured. Pass -DifyDockerRoot or set DIFY_DOCKER_ROOT."
        return
    }
    if (-not (Test-Path -LiteralPath $DifyDockerRoot -PathType Container)) {
        Write-Warning "Dify docker directory not found: $DifyDockerRoot"
        return
    }
    $docker = Get-Command docker -ErrorAction Stop
    Write-Host "[dify] docker compose stop"
    if (-not $DryRun) {
        $process = Start-Process `
            -FilePath $docker.Source `
            -ArgumentList @("compose", "stop") `
            -WorkingDirectory $DifyDockerRoot `
            -NoNewWindow `
            -Wait `
            -PassThru
        if ($process.ExitCode -ne 0) {
            throw "docker compose stop exited with code $($process.ExitCode)"
        }
    }
}
