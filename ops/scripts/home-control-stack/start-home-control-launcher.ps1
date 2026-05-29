param(
    [string]$WorkspaceRoot = "",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8799,
    [ValidateSet("", "manifest_default", "isolated_override")]
    [string]$PortMode = "",
    [string]$StackStateDir = "",
    [switch]$OpenBrowser,
    [switch]$ReuseExisting
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

. (Join-Path $PSScriptRoot "resolve-home-control-workspace.ps1")
$WorkspaceRoot = Resolve-HomeControlWorkspaceRoot -WorkspaceRoot $WorkspaceRoot -ScriptRoot $PSScriptRoot
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..\..")).Path
$LauncherServer = Join-Path $ProjectRoot "tools\home-control-launcher\server.js"
$StopLauncherScript = Join-Path $PSScriptRoot "stop-home-control-launcher.ps1"

if (-not (Test-Path -LiteralPath $LauncherServer -PathType Leaf)) {
    throw "Home Control Launcher server not found: $LauncherServer"
}

$node = Get-Command "node" -ErrorAction SilentlyContinue
if ($null -eq $node) {
    throw "node was not found. Install Node.js or add it to PATH."
}

$env:HOME_CONTROL_WORKSPACE_ROOT = $WorkspaceRoot
$currentPowerShell = Get-Process -Id $PID -ErrorAction SilentlyContinue
if ($null -ne $currentPowerShell -and -not [string]::IsNullOrWhiteSpace($currentPowerShell.Path)) {
    $env:HOME_CONTROL_POWERSHELL = $currentPowerShell.Path
}
$url = "http://$HostName`:$Port"

function Open-LauncherBrowser {
    param([string]$Url)
    try {
        Start-Process $Url | Out-Null
    } catch {
        Write-Host "Open this URL in your browser: $Url"
    }
}

function Get-LauncherListenerPids {
    $connections = Get-NetTCPConnection -LocalPort $Port -State Listen -ErrorAction SilentlyContinue
    if ($null -eq $connections) {
        return @()
    }
    return @($connections | Select-Object -ExpandProperty OwningProcess -Unique)
}

Write-Host "Home Control Launcher"
Write-Host "  URL      : $url"
Write-Host "  Workspace: $WorkspaceRoot"
Write-Host ""

$existingPids = @(Get-LauncherListenerPids)
if ($existingPids.Count -gt 0) {
    if ($ReuseExisting) {
        Write-Host "Home Control Launcher is already running: $url"
        Write-Host "  PID(s): $($existingPids -join ', ')"
        Write-Host "  Stop : .\stop-home-control-launcher.bat"
        Write-Host "  Note : Ctrl+C works only in the terminal that owns the running launcher."
        if ($OpenBrowser) {
            Open-LauncherBrowser -Url $url
        }
        exit 0
    }

    if (-not (Test-Path -LiteralPath $StopLauncherScript -PathType Leaf)) {
        throw "Stop launcher script not found: $StopLauncherScript"
    }

    Write-Host "Existing Home Control Launcher found on this port."
    Write-Host "Restarting it in this terminal so Ctrl+C can stop it."
    & $StopLauncherScript -WorkspaceRoot $WorkspaceRoot -HostName $HostName -Port $Port
    Write-Host ""
}

$launcherArgs = @(
    $LauncherServer,
    "--workspace",
    $WorkspaceRoot,
    "--host",
    $HostName,
    "--port",
    [string]$Port
)
if (-not [string]::IsNullOrWhiteSpace($PortMode)) {
    $launcherArgs += @("--port-mode", $PortMode)
}
if (-not [string]::IsNullOrWhiteSpace($StackStateDir)) {
    $launcherArgs += @("--state-dir", $StackStateDir)
}
if ($OpenBrowser) {
    $launcherArgs += "--open-browser"
}

Write-Host "Home Control Launcher is running in this terminal."
Write-Host "  Stop launcher: Ctrl+C"
Write-Host "  Stop stack   : use the web UI Stop Stack button or .\stop-home-control-stack.bat"
Write-Host ""

& $node.Source @launcherArgs
