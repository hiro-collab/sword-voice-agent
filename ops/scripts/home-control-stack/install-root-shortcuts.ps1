param(
    [string]$WorkspaceRoot = "",
    [switch]$WhatIf
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

. (Join-Path $PSScriptRoot "resolve-home-control-workspace.ps1")
$WorkspaceRoot = Resolve-HomeControlWorkspaceRoot -WorkspaceRoot $WorkspaceRoot -ScriptRoot $PSScriptRoot

$opsScriptRoot = Join-Path $WorkspaceRoot "sword-control-plane\ops\scripts"
$managedScriptRoot = Join-Path $opsScriptRoot "home-control-stack"
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

function New-SystemBatchShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$Command,
        [string]$ExtraArgs = ""
    )

    return @"
@echo off
setlocal
for %%I in ("%~dp0.") do set "WORKSPACE_ROOT=%%~fI"
set "TARGET=%WORKSPACE_ROOT%\sword-control-plane\ops\scripts\system.ps1"
where pwsh >nul 2>nul
if %ERRORLEVEL%==0 (
  pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%TARGET%" $Command -WorkspaceRoot "%WORKSPACE_ROOT%" $ExtraArgs %*
) else (
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%TARGET%" $Command -WorkspaceRoot "%WORKSPACE_ROOT%" $ExtraArgs %*
)
endlocal
"@
}

function New-OpsBatchShortcut {
    param(
        [Parameter(Mandatory = $true)][string]$ScriptName,
        [string]$ExtraArgs = ""
    )

    return @"
@echo off
setlocal
for %%I in ("%~dp0.") do set "WORKSPACE_ROOT=%%~fI"
set "TARGET=%WORKSPACE_ROOT%\sword-control-plane\ops\scripts\home-control-stack\$ScriptName"
where pwsh >nul 2>nul
if %ERRORLEVEL%==0 (
  pwsh -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%TARGET%" -WorkspaceRoot "%WORKSPACE_ROOT%" $ExtraArgs %*
) else (
  powershell -NoLogo -NoProfile -ExecutionPolicy Bypass -File "%TARGET%" -WorkspaceRoot "%WORKSPACE_ROOT%" $ExtraArgs %*
)
endlocal
"@
}

function New-SystemPowerShellShortcut {
    param([Parameter(Mandatory = $true)][string]$Command)

    return @"
`$ErrorActionPreference = "Stop"
`$WorkspaceRoot = (Resolve-Path -LiteralPath (Join-Path `$PSScriptRoot "..")).Path
`$Target = Join-Path `$WorkspaceRoot "sword-control-plane\ops\scripts\system.ps1"

& `$Target $Command -WorkspaceRoot `$WorkspaceRoot @args
if (`$LASTEXITCODE -is [int]) {
    exit `$LASTEXITCODE
}
exit 0
"@
}

function New-OpsPowerShellShortcut {
    param([Parameter(Mandatory = $true)][string]$ScriptName)

    return @"
`$ErrorActionPreference = "Stop"
`$WorkspaceRoot = (Resolve-Path -LiteralPath (Join-Path `$PSScriptRoot "..")).Path
`$Target = Join-Path `$WorkspaceRoot "sword-control-plane\ops\scripts\home-control-stack\$ScriptName"

& `$Target -WorkspaceRoot `$WorkspaceRoot @args
if (`$LASTEXITCODE -is [int]) {
    exit `$LASTEXITCODE
}
exit 0
"@
}

$requiredScripts = @(
    (Join-Path $opsScriptRoot "system.ps1"),
    (Join-Path $managedScriptRoot "run-home-control-fault-e2e.ps1"),
    (Join-Path $managedScriptRoot "start-home-control-launcher.ps1"),
    (Join-Path $managedScriptRoot "stop-home-control-launcher.ps1")
)

foreach ($scriptPath in $requiredScripts) {
    if (-not (Test-Path -LiteralPath $scriptPath -PathType Leaf)) {
        throw "Managed script not found: $scriptPath"
    }
}

$rootPowerShellShortcuts = @(
    @{ Name = "start-home-control-stack.ps1"; Kind = "system"; Command = "start" },
    @{ Name = "status-home-control-stack.ps1"; Kind = "system"; Command = "status" },
    @{ Name = "stop-home-control-stack.ps1"; Kind = "system"; Command = "stop" },
    @{ Name = "run-home-control-fault-e2e.ps1"; Kind = "ops"; ScriptName = "run-home-control-fault-e2e.ps1" },
    @{ Name = "start-home-control-launcher.ps1"; Kind = "ops"; ScriptName = "start-home-control-launcher.ps1" },
    @{ Name = "stop-home-control-launcher.ps1"; Kind = "ops"; ScriptName = "stop-home-control-launcher.ps1" }
)

foreach ($shortcut in $rootPowerShellShortcuts) {
    $text = if ($shortcut.Kind -eq "system") {
        New-SystemPowerShellShortcut -Command $shortcut.Command
    }
    else {
        New-OpsPowerShellShortcut -ScriptName $shortcut.ScriptName
    }
    Write-Utf8NoBomFile `
        -Path (Join-Path $rootScriptDir $shortcut.Name) `
        -Text $text
}

$batchShortcuts = @(
    @{ Name = "start-home-control-stack.bat"; Kind = "system"; Command = "start"; ExtraArgs = "" },
    @{ Name = "status-home-control-stack.bat"; Kind = "system"; Command = "status"; ExtraArgs = "" },
    @{ Name = "stop-home-control-stack.bat"; Kind = "system"; Command = "stop"; ExtraArgs = "" },
    @{ Name = "start-home-control-launcher.bat"; Kind = "ops"; ScriptName = "start-home-control-launcher.ps1"; ExtraArgs = "-OpenBrowser" },
    @{ Name = "stop-home-control-launcher.bat"; Kind = "ops"; ScriptName = "stop-home-control-launcher.ps1"; ExtraArgs = "" }
)

foreach ($shortcut in $batchShortcuts) {
    $text = if ($shortcut.Kind -eq "system") {
        New-SystemBatchShortcut -Command $shortcut.Command -ExtraArgs $shortcut.ExtraArgs
    }
    else {
        New-OpsBatchShortcut -ScriptName $shortcut.ScriptName -ExtraArgs $shortcut.ExtraArgs
    }
    Write-Utf8NoBomFile `
        -Path (Join-Path $WorkspaceRoot $shortcut.Name) `
        -Text $text
}

Write-Host "Home Control root shortcuts installed for $WorkspaceRoot"
