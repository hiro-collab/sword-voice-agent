param(
    [string]$WorkspaceRoot = "",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8799,
    [int]$TimeoutSeconds = 5,
    [switch]$Force
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

. (Join-Path $PSScriptRoot "resolve-home-control-workspace.ps1")
$WorkspaceRoot = Resolve-HomeControlWorkspaceRoot -WorkspaceRoot $WorkspaceRoot -ScriptRoot $PSScriptRoot

function Get-LauncherListeners {
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($null -eq $connections) {
        return @()
    }

    $workspaceNeedle = $WorkspaceRoot.ToLowerInvariant()
    $listeners = @()
    foreach ($processId in ($connections | Select-Object -ExpandProperty OwningProcess -Unique)) {
        if ($processId -le 0) {
            continue
        }
        $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
        if ($null -eq $processInfo) {
            continue
        }
        $commandLine = [string]$processInfo.CommandLine
        $commandLower = $commandLine.ToLowerInvariant()
        $isLauncher =
            $commandLower -match "tools[\\/]+home-control-launcher[\\/]+server\.js"
        $workspaceMatches = $commandLower.Contains($workspaceNeedle)
        $listeners += [pscustomobject]@{
            ProcessId = [int]$processId
            Name = [string]$processInfo.Name
            CommandLine = $commandLine
            IsLauncher = [bool]$isLauncher
            WorkspaceMatches = [bool]$workspaceMatches
        }
    }
    return $listeners
}

function Test-ProcessAlive {
    param([int]$ProcessId)
    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Invoke-LauncherShutdownEndpoint {
    $uri = "http://$HostName`:$Port/api/shutdown"
    try {
        Invoke-RestMethod -Method Post -Uri $uri -TimeoutSec 2 | Out-Null
        return $true
    } catch {
        return $false
    }
}

$listeners = @(Get-LauncherListeners)
if ($listeners.Count -eq 0) {
    Write-Host "Home Control Launcher is not running on $HostName`:$Port."
    exit 0
}

if ($Force) {
    $targets = @($listeners)
} else {
    $targets = @($listeners | Where-Object { $_.IsLauncher -and $_.WorkspaceMatches })
}

if ($targets.Count -eq 0) {
    $summary = ($listeners | ForEach-Object {
        "PID $($_.ProcessId) $($_.Name): $($_.CommandLine)"
    }) -join "`n"
    throw "Port $Port is in use, but it does not look like this workspace's Home Control Launcher. Use -Force only if you intend to stop it.`n$summary"
}

Write-Host "Stopping Home Control Launcher on $HostName`:$Port..."
foreach ($target in $targets) {
    Write-Host "  target PID $($target.ProcessId) $($target.Name)"
}

$usedGracefulShutdown = Invoke-LauncherShutdownEndpoint
if ($usedGracefulShutdown) {
    Write-Host "  graceful shutdown requested"
}

$deadline = (Get-Date).AddSeconds([Math]::Max(1, $TimeoutSeconds))
do {
    Start-Sleep -Milliseconds 200
    $remaining = @($targets | Where-Object { Test-ProcessAlive -ProcessId $_.ProcessId })
} while ($remaining.Count -gt 0 -and (Get-Date) -lt $deadline)

foreach ($target in $remaining) {
    Write-Host "  force stopping PID $($target.ProcessId)"
    Stop-Process -Id $target.ProcessId -Force -ErrorAction SilentlyContinue
}

Start-Sleep -Milliseconds 200
$stillRunning = @($targets | Where-Object { Test-ProcessAlive -ProcessId $_.ProcessId })
if ($stillRunning.Count -gt 0) {
    $pids = ($stillRunning | ForEach-Object { $_.ProcessId }) -join ", "
    throw "Home Control Launcher did not stop: $pids"
}

Write-Host "Home Control Launcher stopped."
