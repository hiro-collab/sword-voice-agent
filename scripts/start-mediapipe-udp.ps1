param(
    [string]$EnvPath = ".env",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8765,
    [int]$DebugEvery = 30,
    [string]$StatusDir = ".cache\sword_voice_agent",
    [switch]$Preview,
    [switch]$SuppressProtobufWarnings,
    [string[]]$PublisherArgs = @(),
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

Import-SwordEnv -EnvPath $EnvPath
$mediapipeRoot = Assert-EnvPath -Name "MEDIAPIPE_SWORD_SIGN_ROOT"

$command = @(
    "uv",
    "run",
    "python",
    "apps/publish_udp.py",
    "--host",
    $HostName,
    "--port",
    [string]$Port,
    "--debug",
    "--debug-every",
    [string]$DebugEvery
)

if ($Preview) {
    $command += "--preview"
}
if ($SuppressProtobufWarnings) {
    $command += "--suppress-protobuf-warnings"
}
if ($PublisherArgs.Count -gt 0) {
    $command += $PublisherArgs
}

Invoke-WithModuleStatus `
    -WorkingDirectory $mediapipeRoot `
    -Command $command `
    -StatusDir $StatusDir `
    -ModuleName "mediapipe_udp_publisher" `
    -ModuleLabel "MediaPipe UDP publisher" `
    -Detail "$HostName`:$Port" `
    -DryRun:$DryRun
