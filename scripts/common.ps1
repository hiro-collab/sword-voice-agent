$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

function Get-SwordRepoRoot {
    return (Resolve-Path -LiteralPath (Join-Path $PSScriptRoot "..")).Path
}

function Get-SwordWorkspaceRoot {
    return (Resolve-Path -LiteralPath (Join-Path (Get-SwordRepoRoot) "..")).Path
}

function Resolve-SwordPath {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [string]$BasePath = (Get-SwordRepoRoot)
    )

    if ([System.IO.Path]::IsPathRooted($Path)) {
        return $Path
    }
    return Join-Path $BasePath $Path
}

function Import-SwordEnv {
    param(
        [string]$EnvPath = ".env"
    )

    $resolved = Resolve-SwordPath -Path $EnvPath
    & (Join-Path $PSScriptRoot "load-env.ps1") -Path $resolved
}

function Assert-NoPlaceholder {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    if ($Value.Contains("<") -or $Value.Contains(">")) {
        throw "$Name still contains a placeholder: $Value"
    }
}

function Assert-EnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name
    )

    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Name is not set. Set it in .env and run scripts\load-env.ps1."
    }
    Assert-NoPlaceholder -Name $Name -Value $value
    return $value
}

function Assert-EnvPath {
    param(
        [Parameter(Mandatory = $true)][string]$Name
    )

    $value = Assert-EnvValue -Name $Name
    $resolved = Resolve-SwordPath -Path $value
    if (-not (Test-Path -LiteralPath $resolved)) {
        throw "$Name path not found: $resolved"
    }
    return (Resolve-Path -LiteralPath $resolved).Path
}

function Set-SwordPythonPath {
    $env:PYTHONPATH = Join-Path (Get-SwordRepoRoot) "src"
}

function New-SwordSharedToken {
    $bytes = [byte[]]::new(32)
    [System.Security.Cryptography.RandomNumberGenerator]::Fill($bytes)
    return [Convert]::ToBase64String($bytes).TrimEnd("=").Replace("+", "-").Replace("/", "_")
}

function Set-SwordAiTalkCoreWebTokenDefault {
    param(
        [switch]$Generate
    )

    $value = [Environment]::GetEnvironmentVariable("AI_TALK_CORE_WEB_TOKEN", "Process")
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        return $value
    }

    $sharedToken = [Environment]::GetEnvironmentVariable(
        "SWORD_VOICE_AGENT_AUTH_TOKEN",
        "Process"
    )
    if (-not [string]::IsNullOrWhiteSpace($sharedToken)) {
        $env:AI_TALK_CORE_WEB_TOKEN = $sharedToken
        return $sharedToken
    }

    if ($Generate) {
        $generated = New-SwordSharedToken
        $env:AI_TALK_CORE_WEB_TOKEN = $generated
        return $generated
    }

    return ""
}

function Format-CommandLine {
    param(
        [Parameter(Mandatory = $true)][string[]]$Command
    )

    return ($Command | ForEach-Object {
        if ($_ -match '[\s"]') {
            '"' + ($_ -replace '"', '\"') + '"'
        }
        else {
            $_
        }
    }) -join " "
}

function Write-SwordModuleStatus {
    param(
        [Parameter(Mandatory = $true)][string]$StatusDir,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$State,
        [string]$Label = "",
        [string]$Detail = ""
    )

    if ($Name -notmatch "^[A-Za-z0-9_-]{1,64}$") {
        throw "invalid module status name: $Name"
    }

    $resolvedStatusDir = Resolve-SwordPath -Path $StatusDir
    $modulesDir = Join-Path $resolvedStatusDir "modules"
    New-Item -ItemType Directory -Force -Path $modulesDir | Out-Null
    $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
    $payload = [ordered]@{
        type = "module_status"
        name = $Name
        label = if ([string]::IsNullOrWhiteSpace($Label)) { $Name } else { $Label }
        state = $State
        detail = $Detail
        timestamp = $timestamp
    }
    $path = Join-Path $modulesDir "$Name.json"
    $payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $path -Encoding UTF8
}

function Test-SwordLoopbackUrl {
    param(
        [Parameter(Mandatory = $true)][string]$Url
    )

    try {
        $uri = [System.Uri]$Url
    }
    catch {
        return $false
    }

    $hostName = $uri.Host.ToLowerInvariant()
    return $hostName -in @("localhost", "127.0.0.1", "::1", "[::1]")
}

function Test-SwordHttpReachable {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$TimeoutMilliseconds = 2000
    )

    if ([string]::IsNullOrWhiteSpace($Url)) {
        return $false
    }

    try {
        $request = [System.Net.WebRequest]::Create($Url)
        $request.Method = "GET"
        $request.Timeout = $TimeoutMilliseconds
        $request.ReadWriteTimeout = $TimeoutMilliseconds
        $response = $request.GetResponse()
        $response.Close()
        return $true
    }
    catch [System.Net.WebException] {
        if ($_.Exception.Response) {
            $_.Exception.Response.Close()
            return $true
        }
        return $false
    }
    catch {
        return $false
    }
}

function Wait-SwordHttpReachable {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [int]$WaitSeconds = 60,
        [string]$Label = "HTTP endpoint"
    )

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds([Math]::Max(1, $WaitSeconds))
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        if (Test-SwordHttpReachable -Url $Url) {
            return $true
        }
        Start-Sleep -Seconds 2
    }

    Write-Warning "$Label is not reachable: $Url"
    return $false
}

function Test-SwordDockerEngine {
    $docker = Get-Command docker -ErrorAction SilentlyContinue
    if ($null -eq $docker) {
        return $false
    }

    try {
        & $docker.Source info *> $null
        return $LASTEXITCODE -eq 0
    }
    catch {
        return $false
    }
}

function Get-SwordDockerDesktopPath {
    $candidates = @()
    if (-not [string]::IsNullOrWhiteSpace($env:ProgramFiles)) {
        $candidates += Join-Path $env:ProgramFiles "Docker\Docker\Docker Desktop.exe"
    }
    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        $candidates += Join-Path $env:LOCALAPPDATA "Docker\Docker Desktop.exe"
    }

    foreach ($candidate in $candidates) {
        if (Test-Path -LiteralPath $candidate) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    $command = Get-Command "Docker Desktop.exe" -ErrorAction SilentlyContinue
    if ($null -ne $command) {
        return $command.Source
    }
    return ""
}

function Wait-SwordDockerEngine {
    param(
        [int]$WaitSeconds = 120
    )

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds([Math]::Max(1, $WaitSeconds))
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        if (Test-SwordDockerEngine) {
            return $true
        }
        Start-Sleep -Seconds 3
    }
    return $false
}

function Ensure-SwordDockerDesktop {
    param(
        [int]$WaitSeconds = 120,
        [switch]$NoStart
    )

    if (Test-SwordDockerEngine) {
        Write-Host "Docker engine is running."
        return
    }

    if ($NoStart) {
        throw "Docker engine is not running. Start Docker Desktop, or rerun without -NoStartDockerDesktop."
    }

    $dockerDesktopPath = Get-SwordDockerDesktopPath
    if ([string]::IsNullOrWhiteSpace($dockerDesktopPath)) {
        throw "Docker Desktop was not found. Start it manually, then rerun this script."
    }

    Write-Host "Docker engine is not running. Starting Docker Desktop..."
    Start-Process -FilePath $dockerDesktopPath -WindowStyle Normal
    if (-not (Wait-SwordDockerEngine -WaitSeconds $WaitSeconds)) {
        throw "Docker engine did not become ready within $WaitSeconds seconds."
    }
}

function Start-SwordModuleHeartbeat {
    param(
        [Parameter(Mandatory = $true)][string]$StatusDir,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Label,
        [string]$Detail = "",
        [double]$IntervalSeconds = 1.0
    )

    $resolvedStatusDir = Resolve-SwordPath -Path $StatusDir
    return Start-Job -ScriptBlock {
        param($StatusDir, $Name, $Label, $Detail, $IntervalSeconds)
        $ErrorActionPreference = "Stop"
        $modulesDir = Join-Path $StatusDir "modules"
        New-Item -ItemType Directory -Force -Path $modulesDir | Out-Null
        $path = Join-Path $modulesDir "$Name.json"
        while ($true) {
            $timestamp = [DateTimeOffset]::UtcNow.ToUnixTimeMilliseconds() / 1000.0
            $payload = [ordered]@{
                type = "module_status"
                name = $Name
                label = $Label
                state = "running"
                detail = $Detail
                timestamp = $timestamp
            }
            $payload | ConvertTo-Json -Depth 6 | Set-Content -LiteralPath $path -Encoding UTF8
            Start-Sleep -Milliseconds ([int]([Math]::Max(0.2, $IntervalSeconds) * 1000))
        }
    } -ArgumentList $resolvedStatusDir, $Name, $Label, $Detail, $IntervalSeconds
}

function Stop-SwordModuleHeartbeat {
    param(
        [object]$Job
    )

    if ($null -eq $Job) {
        return
    }
    Stop-Job -Job $Job -ErrorAction SilentlyContinue | Out-Null
    Remove-Job -Job $Job -Force -ErrorAction SilentlyContinue | Out-Null
}

function Invoke-OrPrint {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string[]]$Command,
        [switch]$DryRun
    )

    if ($DryRun) {
        Write-Host "[dry-run] no process will be started"
        Write-Host "cd $WorkingDirectory"
        Write-Host (Format-CommandLine -Command $Command)
        return
    }

    Push-Location $WorkingDirectory
    try {
        $exe = $Command[0]
        $arguments = @()
        if ($Command.Count -gt 1) {
            $arguments = $Command[1..($Command.Count - 1)]
        }
        & $exe @arguments
    }
    finally {
        Pop-Location
    }
}

function Invoke-WithModuleStatus {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string[]]$Command,
        [Parameter(Mandatory = $true)][string]$StatusDir,
        [Parameter(Mandatory = $true)][string]$ModuleName,
        [Parameter(Mandatory = $true)][string]$ModuleLabel,
        [string]$Detail = "",
        [switch]$DryRun
    )

    if ($DryRun) {
        Invoke-OrPrint -WorkingDirectory $WorkingDirectory -Command $Command -DryRun
        return
    }

    Write-SwordModuleStatus `
        -StatusDir $StatusDir `
        -Name $ModuleName `
        -State "starting" `
        -Label $ModuleLabel `
        -Detail $Detail

    $heartbeat = Start-SwordModuleHeartbeat `
        -StatusDir $StatusDir `
        -Name $ModuleName `
        -Label $ModuleLabel `
        -Detail $Detail
    try {
        Invoke-OrPrint -WorkingDirectory $WorkingDirectory -Command $Command
    }
    finally {
        Stop-SwordModuleHeartbeat -Job $heartbeat
        Write-SwordModuleStatus `
            -StatusDir $StatusDir `
            -Name $ModuleName `
            -State "stopped" `
            -Label $ModuleLabel `
            -Detail "process exited"
    }
}
