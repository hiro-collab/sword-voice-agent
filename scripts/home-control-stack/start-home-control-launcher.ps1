param(
    [string]$WorkspaceRoot = "",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8799,
    [switch]$OpenBrowser
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

. (Join-Path $PSScriptRoot "resolve-home-control-workspace.ps1")
$WorkspaceRoot = Resolve-HomeControlWorkspaceRoot -WorkspaceRoot $WorkspaceRoot -ScriptRoot $PSScriptRoot
$ProjectRoot = (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..\..")).Path
$LauncherServer = Join-Path $ProjectRoot "tools\home-control-launcher\server.js"

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

Write-Host "Home Control Launcher"
Write-Host "  URL      : $url"
Write-Host "  Workspace: $WorkspaceRoot"
Write-Host ""

$launcherArgs = @(
    $LauncherServer,
    "--workspace",
    $WorkspaceRoot,
    "--host",
    $HostName,
    "--port",
    [string]$Port
)
if ($OpenBrowser) {
    $launcherArgs += "--open-browser"
}

& $node.Source @launcherArgs
