param(
    [string]$EnvPath = ".env",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8765,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
Import-SwordEnv -EnvPath $EnvPath
Set-SwordPythonPath

Invoke-OrPrint `
    -WorkingDirectory $repoRoot `
    -Command @(
        "python",
        "-m",
        "sword_voice_agent.apps.send_demo_gestures",
        "--host",
        $HostName,
        "--port",
        [string]$Port,
        "--print-json"
    ) `
    -DryRun:$DryRun
