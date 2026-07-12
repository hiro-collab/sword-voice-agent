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
        "Memory Compression",
        "chrome",
        "chrome.exe",
        "msedge",
        "msedge.exe",
        "firefox",
        "firefox.exe",
        "brave",
        "brave.exe",
        "brave-browser",
        "brave-browser.exe",
        "opera",
        "opera.exe",
        "vivaldi",
        "vivaldi.exe",
        "updater",
        "updater.exe",
        "googleupdate",
        "googleupdate.exe",
        "microsoftedgeupdate",
        "microsoftedgeupdate.exe"
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

function Get-SwordSealedListenerClassSpec {
    param([Parameter(Mandatory = $true)][string]$OwnershipClass)

    switch ($OwnershipClass) {
        "vsp_descendant_listener.v0" {
            return [pscustomobject]@{
                Role = "vision_snapshot_processor_listener"
                ExpectedModule = "vision_snapshot_processor.main"
                ProcessName = "python"
            }
        }
        "home_control_bridge_descendant_listener.v0" {
            return [pscustomobject]@{
                Role = "home_assistant_bridge_listener"
                ExpectedModule = "home_control_bridge.main:app"
                ProcessName = "python"
            }
        }
        "thought_core_descendant_listener.v0" {
            return [pscustomobject]@{
                Role = "thought_core_api_listener"
                ExpectedModule = "thought_core"
                ProcessName = "python"
            }
        }
        default { return $null }
    }
}

function ConvertTo-SwordOwnershipTimestamp {
    param([object]$Value)
    if ($Value -is [DateTimeOffset]) { return ([DateTimeOffset]$Value).ToString("o") }
    if ($Value -is [DateTime]) { return ([DateTime]$Value).ToString("o") }
    return [string]$Value
}

function Get-SwordObjectProperty {
    param([object]$Object, [Parameter(Mandatory = $true)][string]$Name, [object]$Default = $null)
    if ($null -eq $Object) { return $Default }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property -or $null -eq $property.Value) { return $Default }
    return $property.Value
}

function Get-SwordSealedListenerOwnershipSeal {
    param([Parameter(Mandatory = $true)][object]$Entry)
    $parts = @(
        [string](Get-SwordObjectProperty $Entry "ownership_class" "")
        [string][int](Get-SwordObjectProperty $Entry "pid" 0)
        ConvertTo-SwordOwnershipTimestamp (Get-SwordObjectProperty $Entry "started_at" "")
        [string][int](Get-SwordObjectProperty $Entry "ownership_parent_pid" 0)
        [string][int](Get-SwordObjectProperty $Entry "ownership_root_pid" 0)
        ConvertTo-SwordOwnershipTimestamp (Get-SwordObjectProperty $Entry "ownership_root_started_at" "")
        [string](Get-SwordObjectProperty $Entry "expected_module" "")
        [string][int](Get-SwordObjectProperty $Entry "expected_port" 0)
    )
    foreach ($row in @((Get-SwordObjectProperty $Entry "ownership_lineage" @()))) {
        $parts += ("{0}|{1}|{2}|{3}" -f
            [int](Get-SwordObjectProperty $row "pid" 0),
            [int](Get-SwordObjectProperty $row "parent_pid" 0),
            ([string](Get-SwordObjectProperty $row "process_name" "")).ToLowerInvariant().Replace(".exe", ""),
            (ConvertTo-SwordOwnershipTimestamp (Get-SwordObjectProperty $row "started_at" "")))
    }
    $sha = [System.Security.Cryptography.SHA256]::Create()
    try {
        return ([Convert]::ToHexString($sha.ComputeHash([Text.Encoding]::UTF8.GetBytes(($parts -join "`n"))))).ToLowerInvariant()
    }
    finally { $sha.Dispose() }
}

function Test-SwordSealedListenerCommand {
    param([string]$CommandLine, [string]$OwnershipClass, [int]$Port)
    if ([string]::IsNullOrWhiteSpace($CommandLine) -or $Port -le 0) { return $false }
    $portPattern = "(?i)(^|\s)--port\s+{0}(?:\s|$)" -f $Port
    if ($CommandLine -notmatch $portPattern) { return $false }
    switch ($OwnershipClass) {
        "vsp_descendant_listener.v0" { return $CommandLine -match '(?i)(^|\s)-m\s+vision_snapshot_processor\.main(?:\s|$)' }
        "home_control_bridge_descendant_listener.v0" {
            return $CommandLine -match '(?i)(^|\s)-m\s+uvicorn(?:\s|$)' -and
                $CommandLine -match '(?i)(^|\s)home_control_bridge\.main:app(?:\s|$)'
        }
        "thought_core_descendant_listener.v0" { return $CommandLine -match '(?i)(^|\s)-m\s+thought_core(?:\s|$)' }
        default { return $false }
    }
}

function Test-SwordSealedListenerStart {
    param([Parameter(Mandatory = $true)][object]$Process, [string]$RecordedAt)
    try {
        $delta = ([DateTimeOffset]$Process.StartTime) - [DateTimeOffset]::Parse($RecordedAt)
        return [Math]::Abs($delta.TotalMilliseconds) -le 2000
    }
    catch { return $false }
}

function Test-SwordSealedDescendantListenerEntry {
    param(
        [Parameter(Mandatory = $true)][object]$Entry,
        [string]$RequiredOwnershipClass = "",
        [switch]$SimulateInspectionFailure
    )

    $invalid = { param($Reason) [pscustomobject]@{ Valid = $false; Reason = $Reason; ListenerPid = 0; LiveChainPids = @() } }
    if ($SimulateInspectionFailure) { return & $invalid "inspection_failed" }
    $ownershipClass = [string](Get-SwordObjectProperty $Entry "ownership_class" "")
    $spec = Get-SwordSealedListenerClassSpec -OwnershipClass $ownershipClass
    if ($null -eq $spec -or (-not [string]::IsNullOrWhiteSpace($RequiredOwnershipClass) -and $ownershipClass -cne $RequiredOwnershipClass)) {
        return & $invalid "class_invalid"
    }
    $storedSeal = [string](Get-SwordObjectProperty $Entry "ownership_seal" "")
    if ($storedSeal -notmatch '^[0-9a-f]{64}$' -or (Get-SwordSealedListenerOwnershipSeal $Entry) -cne $storedSeal) {
        return & $invalid "seal_invalid"
    }
    if ([string](Get-SwordObjectProperty $Entry "role" "") -cne $spec.Role -or
        [string](Get-SwordObjectProperty $Entry "expected_module" "") -cne $spec.ExpectedModule) {
        return & $invalid "service_invalid"
    }
    $listenerPid = [int](Get-SwordObjectProperty $Entry "pid" 0)
    $parentPid = [int](Get-SwordObjectProperty $Entry "ownership_parent_pid" 0)
    $rootPid = [int](Get-SwordObjectProperty $Entry "ownership_root_pid" 0)
    $port = [int](Get-SwordObjectProperty $Entry "expected_port" 0)
    if ($listenerPid -le 0 -or $parentPid -le 0 -or $rootPid -le 0 -or $port -le 0) { return & $invalid "correlation_invalid" }
    try {
        $listener = Get-Process -Id $listenerPid -ErrorAction Stop
        $identity = Get-CimInstance Win32_Process -Filter "ProcessId = $listenerPid" -ErrorAction Stop
        $owners = @(Get-NetTCPConnection -LocalPort $port -State Listen -ErrorAction Stop |
            Where-Object { [string]$_.LocalAddress -in @("127.0.0.1", "::1") } |
            Select-Object -ExpandProperty OwningProcess -Unique)
    }
    catch { return & $invalid "inspection_failed" }
    if ($owners.Count -ne 1 -or [int]$owners[0] -ne $listenerPid) { return & $invalid "port_owner_invalid" }
    $processName = ([string]$listener.ProcessName).ToLowerInvariant().Replace(".exe", "")
    if ($processName -cne $spec.ProcessName -or [int]$identity.ParentProcessId -ne $parentPid -or
        -not (Test-SwordSealedListenerStart $listener ([string](Get-SwordObjectProperty $Entry "started_at" ""))) -or
        -not (Test-SwordSealedListenerCommand ([string]$identity.CommandLine) $ownershipClass $port)) {
        return & $invalid "listener_identity_invalid"
    }
    $lineage = @((Get-SwordObjectProperty $Entry "ownership_lineage" @()))
    $rootStartedAt = ConvertTo-SwordOwnershipTimestamp (Get-SwordObjectProperty $Entry "ownership_root_started_at" "")
    if ($lineage.Count -eq 0 -or $lineage.Count -gt 8 -or
        [int](Get-SwordObjectProperty $lineage[0] "pid" 0) -ne $parentPid -or
        [int](Get-SwordObjectProperty $lineage[-1] "pid" 0) -ne $rootPid -or
        (ConvertTo-SwordOwnershipTimestamp (Get-SwordObjectProperty $lineage[-1] "started_at" "")) -cne $rootStartedAt) {
        return & $invalid "lineage_invalid"
    }
    $liveChain = @($listenerPid)
    for ($index = 0; $index -lt $lineage.Count; $index++) {
        $row = $lineage[$index]
        $rowPid = [int](Get-SwordObjectProperty $row "pid" 0)
        $rowParentPid = [int](Get-SwordObjectProperty $row "parent_pid" 0)
        $rowName = ([string](Get-SwordObjectProperty $row "process_name" "")).ToLowerInvariant().Replace(".exe", "")
        $rowStartedAt = ConvertTo-SwordOwnershipTimestamp (Get-SwordObjectProperty $row "started_at" "")
        if ($rowPid -le 0 -or $rowParentPid -le 0 -or [string]::IsNullOrWhiteSpace($rowName) -or [string]::IsNullOrWhiteSpace($rowStartedAt)) {
            return & $invalid "lineage_invalid"
        }
        if ($index + 1 -lt $lineage.Count -and $rowParentPid -ne [int](Get-SwordObjectProperty $lineage[$index + 1] "pid" 0)) {
            return & $invalid "lineage_invalid"
        }
        $runtime = Get-Process -Id $rowPid -ErrorAction SilentlyContinue
        $runtimeIdentity = Get-CimInstance Win32_Process -Filter "ProcessId = $rowPid" -ErrorAction SilentlyContinue
        if ($null -ne $runtime -or $null -ne $runtimeIdentity) {
            if ($null -eq $runtime -or $null -eq $runtimeIdentity -or
                ([string]$runtime.ProcessName).ToLowerInvariant().Replace(".exe", "") -cne $rowName -or
                -not (Test-SwordSealedListenerStart $runtime $rowStartedAt) -or
                [int]$runtimeIdentity.ParentProcessId -ne $rowParentPid) {
                return & $invalid "lineage_changed"
            }
            $liveChain += $rowPid
        }
    }
    return [pscustomobject]@{ Valid = $true; Reason = "owned"; ListenerPid = $listenerPid; LiveChainPids = @($liveChain | Sort-Object -Unique -Descending) }
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

function Write-SwordControllerManifest {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$LaunchNonce,
        [Parameter(Mandatory = $true)][string]$ServiceClass,
        [Parameter(Mandatory = $true)][System.Diagnostics.Process]$Controller,
        [Parameter(Mandatory = $true)][string]$ExpectedModule,
        [Parameter(Mandatory = $true)][int]$ExpectedPort
    )

    $temporaryPath = "${Path}.$PID.tmp"
    $forceWriteFailure = $false
    try {
        if ($LaunchNonce -notmatch "^[a-f0-9]{32}$") {
            throw "invalid"
        }
        $directory = Split-Path -Parent $Path
        if ([string]::IsNullOrWhiteSpace($directory)) {
            throw "invalid"
        }
        New-Item -ItemType Directory -Path $directory -Force | Out-Null
        $controllerStartedAt = ([DateTimeOffset]$Controller.StartTime).ToString("o")
        $payload = [ordered]@{
            schema_version = "thought_core_controller.v1"
            service_class = $ServiceClass
            launch_nonce = $LaunchNonce
            controller_pid = [int]$Controller.Id
            controller_started_at = $controllerStartedAt
            expected_module = $ExpectedModule
            expected_port = $ExpectedPort
            written_at = [DateTimeOffset]::UtcNow.ToString("o")
        }
        if ($env:NODE_ENV -eq "test") {
            switch ([string]$env:HOME_CONTROL_STACK_TEST_THOUGHT_CORE_MANIFEST_MUTATION) {
                "missing" { return }
                "partial" { $payload.Remove("expected_module") }
                "stale" { $payload["written_at"] = "2000-01-01T00:00:00+00:00" }
                "nonce" { $payload["launch_nonce"] = "0" * 32 }
                "controller_start" { $payload["controller_started_at"] = "2000-01-01T00:00:00+00:00" }
                "service_class" { $payload["service_class"] = "unrelated_service" }
                "module" { $payload["expected_module"] = "unrelated.module" }
                "port" { $payload["expected_port"] = $ExpectedPort + 1 }
                "controller_pid" {
                    $replacementPid = 0
                    if ([int]::TryParse(
                        [string]$env:HOME_CONTROL_STACK_TEST_THOUGHT_CORE_MANIFEST_CONTROLLER_PID,
                        [ref]$replacementPid
                    ) -and $replacementPid -gt 0) {
                        $replacement = Get-Process -Id $replacementPid -ErrorAction Stop
                        $payload["controller_pid"] = $replacementPid
                        $payload["controller_started_at"] = ([DateTimeOffset]$replacement.StartTime).ToString("o")
                    }
                }
                "write_failure" { $forceWriteFailure = $true }
            }
        }
        $json = $payload | ConvertTo-Json -Depth 3
        if ([Text.Encoding]::UTF8.GetByteCount($json) -gt 4096) {
            throw "invalid"
        }
        [IO.File]::WriteAllText($temporaryPath, $json, [Text.UTF8Encoding]::new($false))
        if ($forceWriteFailure) {
            $pidFile = [string]$env:HOME_CONTROL_STACK_TEST_THOUGHT_CORE_PID_FILE
            $deadline = [DateTime]::UtcNow.AddSeconds(2)
            while (
                -not [string]::IsNullOrWhiteSpace($pidFile) -and
                -not (Test-Path -LiteralPath $pidFile -PathType Leaf) -and
                [DateTime]::UtcNow -lt $deadline
            ) {
                Start-Sleep -Milliseconds 25
            }
            throw [IO.IOException]::new("fixture_write_failure")
        }
        Move-Item -LiteralPath $temporaryPath -Destination $Path -Force
    }
    catch {
        Remove-Item -LiteralPath $temporaryPath -Force -ErrorAction SilentlyContinue
        throw "thought_core_controller_manifest_write_failed"
    }
}

function Invoke-SwordControllerCommand {
    param(
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][string[]]$Command,
        [Parameter(Mandatory = $true)][string]$ManifestPath,
        [Parameter(Mandatory = $true)][string]$LaunchNonce,
        [Parameter(Mandatory = $true)][string]$ServiceClass,
        [Parameter(Mandatory = $true)][string]$ExpectedModule,
        [Parameter(Mandatory = $true)][int]$ExpectedPort
    )

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $Command[0]
    $startInfo.WorkingDirectory = $WorkingDirectory
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $argumentListProperty = $startInfo.GetType().GetProperty("ArgumentList")
    if ($null -eq $argumentListProperty) {
        throw "thought_core_controller_start_failed"
    }
    foreach ($argument in @($Command | Select-Object -Skip 1)) {
        $startInfo.ArgumentList.Add($argument)
    }
    if (
        $env:NODE_ENV -eq "test" -and
        $env:HOME_CONTROL_STACK_TEST_THOUGHT_CORE_PRIVATE_ENV_INJECTION -eq "true"
    ) {
        $startInfo.Environment["SWORD_THOUGHT_CORE_CONTROLLER_MANIFEST"] = "fixture_private_value"
        $startInfo.Environment["SWORD_THOUGHT_CORE_LAUNCH_NONCE"] = "fixture_private_value"
    }
    $startInfo.Environment.Remove("SWORD_THOUGHT_CORE_CONTROLLER_MANIFEST") | Out-Null
    $startInfo.Environment.Remove("SWORD_THOUGHT_CORE_LAUNCH_NONCE") | Out-Null
    $controller = [System.Diagnostics.Process]::new()
    $controller.StartInfo = $startInfo
    $controllerStarted = $false
    $controllerExitedNormally = $false
    $controllerStartedAtTicks = [long]0
    try {
        if (-not $controller.Start()) {
            throw "thought_core_controller_start_failed"
        }
        $controllerStarted = $true
        $controllerStartedAtTicks = $controller.StartTime.ToUniversalTime().Ticks
        Write-SwordControllerManifest `
            -Path $ManifestPath `
            -LaunchNonce $LaunchNonce `
            -ServiceClass $ServiceClass `
            -Controller $controller `
            -ExpectedModule $ExpectedModule `
            -ExpectedPort $ExpectedPort
        $controller.WaitForExit()
        $controllerExitedNormally = $true
        return [int]$controller.ExitCode
    }
    catch {
        $failureClass = "thought_core_controller_start_failed"
        if ($_.Exception.Message -match "^thought_core_controller_") {
            $failureClass = $_.Exception.Message
        }
        if ($controllerStarted -and -not $controllerExitedNormally) {
            $cleanupComplete = $false
            try {
                $controller.Refresh()
                if ($controller.HasExited) {
                    $cleanupComplete = $true
                }
                elseif (
                    $controllerStartedAtTicks -gt 0 -and
                    $controller.StartTime.ToUniversalTime().Ticks -eq $controllerStartedAtTicks
                ) {
                    $controller.Kill($true)
                    $cleanupComplete = $controller.WaitForExit(5000)
                    if ($cleanupComplete) {
                        Start-Sleep -Milliseconds 100
                    }
                }
            }
            catch {
                $cleanupComplete = $false
            }
            if (-not $cleanupComplete) {
                throw "thought_core_controller_cleanup_incomplete"
            }
        }
        throw $failureClass
    }
    finally {
        $controller.Dispose()
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
        [string]$ControllerManifestPath = "",
        [string]$ControllerLaunchNonce = "",
        [string]$ControllerServiceClass = "",
        [string]$ControllerExpectedModule = "",
        [int]$ControllerExpectedPort = 0,
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
        if (-not [string]::IsNullOrWhiteSpace($ControllerManifestPath)) {
            $controllerExitCode = Invoke-SwordControllerCommand `
                -WorkingDirectory $WorkingDirectory `
                -Command $Command `
                -ManifestPath $ControllerManifestPath `
                -LaunchNonce $ControllerLaunchNonce `
                -ServiceClass $ControllerServiceClass `
                -ExpectedModule $ControllerExpectedModule `
                -ExpectedPort $ControllerExpectedPort
            if ($controllerExitCode -ne 0) {
                throw "thought_core_controller_exited"
            }
        }
        else {
            Invoke-OrPrint -WorkingDirectory $WorkingDirectory -Command $Command
        }
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
