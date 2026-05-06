param(
    [string]$WorkspaceRoot = "",
    [string]$DifyDockerRoot = "C:\Users\kawai\works\dify\docker",
    [switch]$StopDify,
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "resolve-home-control-workspace.ps1")
$WorkspaceRoot = Resolve-HomeControlWorkspaceRoot -WorkspaceRoot $WorkspaceRoot -ScriptRoot $PSScriptRoot

$StateDir = Join-Path $WorkspaceRoot ".cache\home-control-stack"
$PidFile = Join-Path $StateDir "pids.json"

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

function Get-DescendantProcessIds {
    param([Parameter(Mandatory = $true)][int[]]$RootProcessIds)
    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $queue = [System.Collections.Generic.Queue[int]]::new()
    $seen = [System.Collections.Generic.HashSet[int]]::new()
    foreach ($pidValue in $RootProcessIds) {
        if ($seen.Add($pidValue)) {
            $queue.Enqueue($pidValue)
        }
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

function Stop-ProcessTree {
    param([Parameter(Mandatory = $true)][int[]]$RootProcessIds)
    $targetIds = @(Get-DescendantProcessIds -RootProcessIds $RootProcessIds | Sort-Object -Descending)
    foreach ($pidValue in $targetIds) {
        if ($pidValue -eq $PID) {
            continue
        }
        $process = Get-Process -Id $pidValue -ErrorAction SilentlyContinue
        if ($null -eq $process) {
            continue
        }
        if ($DryRun) {
            Write-Host "[dry-run] stop PID $pidValue $($process.ProcessName)"
            continue
        }
        try {
            Stop-Process -Id $pidValue -Force -ErrorAction Stop
            Write-Host "stopped PID $pidValue $($process.ProcessName)"
        }
        catch {
            Write-Warning "failed to stop PID $pidValue $($process.ProcessName): $($_.Exception.Message)"
        }
    }
}

$state = Read-PidState
$rootPids = @()
if ($null -ne $state -and $null -ne $state.processes) {
    foreach ($entry in $state.processes) {
        $pidValue = [int]$entry.pid
        if ($null -ne (Get-Process -Id $pidValue -ErrorAction SilentlyContinue)) {
            $rootPids += $pidValue
        }
    }
}

if ($rootPids.Count -eq 0) {
    Write-Host "No recorded home-control stack processes are running."
}
else {
    Write-Host "Target root PIDs: $($rootPids -join ', ')"
    if (-not $Force -and -not $DryRun) {
        $answer = Read-Host "Stop these processes? [y/N]"
        if ($answer -notmatch "^(y|yes)$") {
            Write-Host "Canceled."
            return
        }
    }
    Stop-ProcessTree -RootProcessIds $rootPids
}

if (-not $DryRun -and (Test-Path -LiteralPath $PidFile -PathType Leaf)) {
    Remove-Item -LiteralPath $PidFile -Force
}

if ($StopDify) {
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
