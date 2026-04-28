param(
    [string]$EnvPath = ".env",
    [string]$Source = "web",
    [string]$Field = "command",
    [string]$StatusDir = ".cache\sword_voice_agent",
    [switch]$NoSkipExisting,
    [switch]$PrintJson,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
Import-SwordEnv -EnvPath $EnvPath
Set-SwordPythonPath
Assert-EnvPath -Name "AI_TALK_CORE_ROOT" | Out-Null
Assert-EnvValue -Name "DIFY_BASE_URL" | Out-Null
Assert-EnvValue -Name "DIFY_API_KEY" | Out-Null

$command = @(
    "python",
    "-m",
    "sword_voice_agent.apps.watch_handoff_to_dify",
    "--source",
    $Source,
    "--field",
    $Field,
    "--status-dir",
    (Resolve-SwordPath -Path $StatusDir)
)

if (-not $NoSkipExisting) {
    $command += "--skip-existing"
}
if ($PrintJson) {
    $command += "--print-json"
}

Invoke-WithModuleStatus `
    -WorkingDirectory $repoRoot `
    -Command $command `
    -StatusDir $StatusDir `
    -ModuleName "dify_watcher" `
    -ModuleLabel "Dify watcher" `
    -Detail "source=$Source field=$Field" `
    -DryRun:$DryRun
