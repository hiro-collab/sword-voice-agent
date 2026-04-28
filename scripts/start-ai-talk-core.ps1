param(
    [string]$EnvPath = ".env",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8000,
    [string]$StatusDir = ".cache\sword_voice_agent",
    [switch]$NoIntegrationDefaults,
    [switch]$NoRecordGateAuto,
    [switch]$NoSaveHandoff,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

Import-SwordEnv -EnvPath $EnvPath
Set-SwordAiTalkCoreWebTokenDefault -Generate | Out-Null
$repoRoot = Get-SwordRepoRoot
$aiTalkCoreRoot = Assert-EnvPath -Name "AI_TALK_CORE_ROOT"
$pythonPathItems = @(
    (Join-Path $repoRoot "src"),
    $aiTalkCoreRoot
)
if (-not [string]::IsNullOrWhiteSpace($env:PYTHONPATH)) {
    $pythonPathItems += $env:PYTHONPATH
}
$env:PYTHONPATH = $pythonPathItems -join [System.IO.Path]::PathSeparator

$command = @(
    "-m",
    "sword_voice_agent.apps.ai_talk_core_web",
    "--ai-talk-core-root",
    $aiTalkCoreRoot,
    "--host",
    $HostName,
    "--port",
    [string]$Port
)
$venvPython = Join-Path $aiTalkCoreRoot ".venv\Scripts\python.exe"
if (Test-Path -LiteralPath $venvPython) {
    $command = @($venvPython) + $command
}
else {
    $command = @("uv", "run", "python") + $command
}

if ($NoIntegrationDefaults -or $NoRecordGateAuto) {
    $command += "--no-record-gate-auto"
}
else {
    $command += "--record-gate-auto"
}

if ($NoIntegrationDefaults -or $NoSaveHandoff) {
    $command += "--no-save-handoff"
}
else {
    $command += "--save-handoff"
}

if ($DryRun) {
    Invoke-OrPrint -WorkingDirectory $aiTalkCoreRoot -Command $command -DryRun
    return
}

# Do not use a background heartbeat here. ai_talk_core is marked running by
# the console when its input-gate API becomes reachable, and avoiding a
# PowerShell background job makes Ctrl+C shutdown much less fragile.
Write-SwordModuleStatus `
    -StatusDir $StatusDir `
    -Name "ai_talk_core" `
    -Label "ai_talk_core Web UI" `
    -State "starting" `
    -Detail "http://$HostName`:$Port"

try {
    Invoke-OrPrint -WorkingDirectory $aiTalkCoreRoot -Command $command
}
finally {
    Write-SwordModuleStatus `
        -StatusDir $StatusDir `
        -Name "ai_talk_core" `
        -Label "ai_talk_core Web UI" `
        -State "stopped" `
        -Detail "process exited"
}
