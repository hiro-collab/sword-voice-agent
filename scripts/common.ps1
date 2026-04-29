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

function Resolve-SwordAvatarModelUrl {
    param(
        [string]$ModelUrl = ""
    )

    $fallback = "/models/default.vrm"
    $trimmed = if ([string]::IsNullOrWhiteSpace($ModelUrl)) { "" } else { $ModelUrl.Trim() }
    if (-not [string]::IsNullOrWhiteSpace($trimmed) -and $trimmed -ne $fallback) {
        return $trimmed
    }

    $avatarRootValue = [Environment]::GetEnvironmentVariable("AVATAR_SERVICE_ROOT", "Process")
    if ([string]::IsNullOrWhiteSpace($avatarRootValue)) {
        if ($trimmed) {
            return $trimmed
        }
        return $fallback
    }

    $avatarRoot = Resolve-SwordPath -Path $avatarRootValue
    $modelsDir = Join-Path $avatarRoot "public\models"
    if (-not (Test-Path -LiteralPath $modelsDir -PathType Container)) {
        if ($trimmed) {
            return $trimmed
        }
        return $fallback
    }

    $defaultModelPath = Join-Path $modelsDir "default.vrm"
    if (Test-Path -LiteralPath $defaultModelPath -PathType Leaf) {
        return $fallback
    }

    $models = @(
        Get-ChildItem `
            -LiteralPath $modelsDir `
            -Filter "*.vrm" `
            -File `
            -ErrorAction SilentlyContinue |
        Sort-Object Name
    )
    if ($models.Count -eq 0) {
        if ($trimmed) {
            return $trimmed
        }
        return $fallback
    }

    $selected = "/models/$([System.Uri]::EscapeDataString($models[0].Name))"
    if ($trimmed -eq $fallback) {
        Write-Warning "AVATAR_MODEL_URL points to $fallback, but default.vrm was not found. Using $selected."
    }
    return $selected
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

function Get-SwordProcessDetail {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId
    )

    $process = Get-Process -Id $ProcessId -ErrorAction SilentlyContinue
    $cim = Get-CimInstance Win32_Process -Filter "ProcessId = $ProcessId" -ErrorAction SilentlyContinue
    return [pscustomobject]@{
        PID = $ProcessId
        ProcessName = if ($process) { $process.ProcessName } else { "" }
        CommandLine = if ($cim) { $cim.CommandLine } else { "" }
        ParentProcessId = if ($cim) { $cim.ParentProcessId } else { $null }
    }
}

function Test-SwordProtectedProcess {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId,
        [string]$ProcessName = ""
    )

    if ($ProcessId -le 4) {
        return $true
    }
    $protectedNames = @(
        "Idle",
        "System",
        "Secure System",
        "Registry",
        "smss",
        "csrss",
        "wininit",
        "services",
        "lsass",
        "Memory Compression"
    )
    return $ProcessName -in $protectedNames
}

function Get-SwordPortUsers {
    param(
        [Parameter(Mandatory = $true)][ValidateSet("TCP", "UDP")][string]$Protocol,
        [Parameter(Mandatory = $true)][int[]]$Ports
    )

    $records = @()
    foreach ($port in $Ports) {
        if ($Protocol -eq "TCP") {
            $listeners = Get-NetTCPConnection `
                -LocalPort $port `
                -State Listen `
                -ErrorAction SilentlyContinue
        }
        else {
            $listeners = Get-NetUDPEndpoint `
                -LocalPort $port `
                -ErrorAction SilentlyContinue
        }
        foreach ($listener in @($listeners)) {
            $detail = Get-SwordProcessDetail -ProcessId ([int]$listener.OwningProcess)
            $records += [pscustomobject]@{
                Protocol = $Protocol
                LocalAddress = $listener.LocalAddress
                LocalPort = $listener.LocalPort
                PID = $detail.PID
                ProcessName = $detail.ProcessName
                CommandLine = $detail.CommandLine
            }
        }
    }
    return $records
}

function Show-SwordPortConflictHelp {
    param(
        [Parameter(Mandatory = $true)][object[]]$Conflicts,
        [string]$StopScript = ".\scripts\stop-full-stack.ps1 -Force"
    )

    Write-Host "Required port is already in use." -ForegroundColor Yellow
    $Conflicts |
        Select-Object Protocol, LocalAddress, LocalPort, PID, ProcessName |
        Format-Table -AutoSize |
        Out-String |
        Write-Host

    Write-Host "The port user is shown for diagnosis only." -ForegroundColor Yellow
    Write-Host "If it is a stale Sword Voice Agent process, run:"
    Write-Host "  $StopScript"
    foreach ($conflict in @($Conflicts | Sort-Object PID -Unique)) {
        if (Test-SwordProtectedProcess `
                -ProcessId ([int]$conflict.PID) `
                -ProcessName ([string]$conflict.ProcessName)) {
            Write-Host "  # PID $($conflict.PID) $($conflict.ProcessName) is a protected Windows process; inspect the port manually."
            continue
        }
        Write-Host "  # Inspect PID $($conflict.PID): Get-CimInstance Win32_Process -Filter `"ProcessId = $($conflict.PID)`" | Select-Object ProcessId,CommandLine"
    }
}

function Assert-SwordPortsAvailable {
    param(
        [int[]]$TcpPorts = @(),
        [int[]]$UdpPorts = @(),
        [string]$StopScript = ".\scripts\stop-full-stack.ps1 -Force"
    )

    $conflicts = @()
    if ($TcpPorts.Count -gt 0) {
        $conflicts += Get-SwordPortUsers -Protocol TCP -Ports $TcpPorts
    }
    if ($UdpPorts.Count -gt 0) {
        $conflicts += Get-SwordPortUsers -Protocol UDP -Ports $UdpPorts
    }
    if ($conflicts.Count -eq 0) {
        return
    }

    Show-SwordPortConflictHelp -Conflicts $conflicts -StopScript $StopScript
    throw "required port is already in use"
}

function Get-SwordDescendantProcessIds {
    param(
        [Parameter(Mandatory = $true)][int[]]$RootProcessIds
    )

    $all = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
    $childrenByParent = @{}
    foreach ($process in $all) {
        $parent = [int]$process.ParentProcessId
        if (-not $childrenByParent.ContainsKey($parent)) {
            $childrenByParent[$parent] = @()
        }
        $childrenByParent[$parent] += [int]$process.ProcessId
    }

    $seen = @{}
    $queue = [System.Collections.Generic.Queue[int]]::new()
    foreach ($rootProcessId in $RootProcessIds) {
        if (Test-SwordProtectedProcess -ProcessId $rootProcessId) {
            continue
        }
        $queue.Enqueue($rootProcessId)
    }
    while ($queue.Count -gt 0) {
        $queuedProcessId = $queue.Dequeue()
        $detail = Get-SwordProcessDetail -ProcessId $queuedProcessId
        if (Test-SwordProtectedProcess `
                -ProcessId $queuedProcessId `
                -ProcessName ([string]$detail.ProcessName)) {
            continue
        }
        if ($seen.ContainsKey($queuedProcessId)) {
            continue
        }
        $seen[$queuedProcessId] = $true
        foreach ($childProcessId in @($childrenByParent[$queuedProcessId])) {
            $queue.Enqueue($childProcessId)
        }
    }
    return @($seen.Keys | ForEach-Object { [int]$_ })
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
