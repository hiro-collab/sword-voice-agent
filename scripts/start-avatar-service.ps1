param(
    [string]$EnvPath = ".env",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 5173,
    [string]$StatusDir = ".cache\sword_voice_agent",
    [string]$RuntimeStatusFile = "",
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

Import-SwordEnv -EnvPath $EnvPath
$avatarServiceRoot = Assert-EnvPath -Name "AVATAR_SERVICE_ROOT"
if ([string]::IsNullOrWhiteSpace($RuntimeStatusFile)) {
    $RuntimeStatusFile = [Environment]::GetEnvironmentVariable(
        "AVATAR_SERVICE_RUNTIME_STATUS_FILE",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($RuntimeStatusFile)) {
    $RuntimeStatusFile = Join-Path $StatusDir "runtime\avatar_service.json"
}
$resolvedRuntimeStatusFile = Resolve-SwordPath -Path $RuntimeStatusFile -BasePath (Get-SwordRepoRoot)
if (-not $DryRun) {
    Assert-SwordPortsAvailable -TcpPorts @($Port)
    $runtimeStatusDir = Split-Path -Parent $resolvedRuntimeStatusFile
    if (-not [string]::IsNullOrWhiteSpace($runtimeStatusDir)) {
        New-Item -ItemType Directory -Force -Path $runtimeStatusDir | Out-Null
    }
}

$command = @(
    "node",
    "scripts/dev-server.mjs",
    "run",
    "--host",
    $HostName,
    "--port",
    [string]$Port,
    "--runtime-status-file",
    $resolvedRuntimeStatusFile
)

Invoke-WithModuleStatus `
    -WorkingDirectory $avatarServiceRoot `
    -Command $command `
    -StatusDir $StatusDir `
    -ModuleName "avatar_service" `
    -ModuleLabel "Avatar service" `
    -Detail "http://$HostName`:$Port" `
    -DryRun:$DryRun
