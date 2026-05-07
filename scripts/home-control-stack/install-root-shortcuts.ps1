param(
    [string]$WorkspaceRoot = "",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "resolve-home-control-workspace.ps1")
$WorkspaceRoot = Resolve-HomeControlWorkspaceRoot -WorkspaceRoot $WorkspaceRoot -ScriptRoot $PSScriptRoot

$managedScriptRoot = Join-Path $WorkspaceRoot "sword-voice-agent\scripts\home-control-stack"
$rootScriptDir = Join-Path $WorkspaceRoot "scripts"

function Write-Utf8NoBomFile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Text
    )

    if ($WhatIf) {
        Write-Host "would write $Path"
        return
    }

    $parent = Split-Path -Parent $Path
    New-Item -ItemType Directory -Force -Path $parent | Out-Null
    $encoding = [System.Text.UTF8Encoding]::new($false)
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

function New-BatchShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptName,
        [string]$ExtraArgs = ""
    )

    return @"
@echo off
setlocal
for %%I in ("%~dp0.") do set "WORKSPACE_ROOT=%%~fI"
set "TARGET=%WORKSPACE_ROOT%\sword-voice-agent\scripts\home-control-stack\$ScriptName"
where pwsh >nul 2>nul
if %ERRORLEVEL%==0 (
  pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%TARGET%" -WorkspaceRoot "%WORKSPACE_ROOT%" $ExtraArgs %*
) else (
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%TARGET%" -WorkspaceRoot "%WORKSPACE_ROOT%" $ExtraArgs %*
)
endlocal
"@
}

function New-PowerShellShortcut {
    param([Parameter(Mandatory = $true)][string]$ScriptName)

    return @"
`$ErrorActionPreference = "Stop"
`$WorkspaceRoot = (Resolve-Path -LiteralPath (Join-Path `$PSScriptRoot "..")).Path
`$Target = Join-Path `$WorkspaceRoot "sword-voice-agent\scripts\home-control-stack\$ScriptName"

& `$Target -WorkspaceRoot `$WorkspaceRoot @args
if (`$LASTEXITCODE -is [int]) {
    exit `$LASTEXITCODE
}
exit 0
"@
}

$scriptNames = @(
    "start-home-control-stack.ps1",
    "status-home-control-stack.ps1",
    "stop-home-control-stack.ps1",
    "run-home-control-fault-e2e.ps1",
    "start-home-control-launcher.ps1"
)

foreach ($scriptName in $scriptNames) {
    $managedPath = Join-Path $managedScriptRoot $scriptName
    if (-not (Test-Path -LiteralPath $managedPath -PathType Leaf)) {
        throw "Managed script not found: $managedPath"
    }

    Write-Utf8NoBomFile `
        -Path (Join-Path $rootScriptDir $scriptName) `
        -Text (New-PowerShellShortcut -ScriptName $scriptName)
}

$batchShortcuts = @(
    @{ Name = "start-home-control-stack.bat"; ScriptName = "start-home-control-stack.ps1"; ExtraArgs = "" },
    @{ Name = "status-home-control-stack.bat"; ScriptName = "status-home-control-stack.ps1"; ExtraArgs = "" },
    @{ Name = "stop-home-control-stack.bat"; ScriptName = "stop-home-control-stack.ps1"; ExtraArgs = "" },
    @{ Name = "start-home-control-launcher.bat"; ScriptName = "start-home-control-launcher.ps1"; ExtraArgs = "-OpenBrowser" }
)

foreach ($shortcut in $batchShortcuts) {
    Write-Utf8NoBomFile `
        -Path (Join-Path $WorkspaceRoot $shortcut.Name) `
        -Text (New-BatchShortcut -ScriptName $shortcut.ScriptName -ExtraArgs $shortcut.ExtraArgs)
}

Write-Host "Home Control root shortcuts installed for $WorkspaceRoot"
