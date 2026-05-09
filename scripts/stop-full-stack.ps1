param(
    [string]$EnvPath = ".env",
    [int]$AiTalkCorePort = 8000,
    [int]$GesturePort = 8765,
    [int]$ConsolePort = 8790,
    [int]$AvatarPort = 5173,
    [string]$TtsHttpPort = "",
    [string]$MediapipeControlHttpPort = "",
    [string]$StatusDir = ".cache\sword_voice_agent",
    [switch]$IncludeDify,
    [switch]$Force,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
Import-SwordEnv -EnvPath $EnvPath

if ([string]::IsNullOrWhiteSpace($TtsHttpPort)) {
    $TtsHttpPort = [Environment]::GetEnvironmentVariable("TTS_HTTP_PORT", "Process")
}
if ([string]::IsNullOrWhiteSpace($TtsHttpPort)) {
    $TtsHttpPort = "8765"
}
if ([string]::IsNullOrWhiteSpace($MediapipeControlHttpPort)) {
    $MediapipeControlHttpPort = [Environment]::GetEnvironmentVariable(
        "MEDIAPIPE_SWORD_SIGN_CONTROL_HTTP_PORT",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($MediapipeControlHttpPort)) {
    $MediapipeControlHttpPort = "18765"
}

function Resolve-RuntimeStatusFile {
    param(
        [Parameter(Mandatory = $true)][string]$EnvName,
        [Parameter(Mandatory = $true)][string]$DefaultPath
    )

    $value = [Environment]::GetEnvironmentVariable($EnvName, "Process")
    if ([string]::IsNullOrWhiteSpace($value)) {
        $value = $DefaultPath
    }
    return Resolve-SwordPath -Path $value -BasePath $repoRoot
}

function Read-SwordRuntimeStatus {
    param(
        [Parameter(Mandatory = $true)][string]$Path
    )

    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return $null
    }
    try {
        return Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    catch {
        Write-Warning "failed to read runtime status: $Path ($($_.Exception.Message))"
        return $null
    }
}

function Test-SwordProcessAlive {
    param(
        [Parameter(Mandatory = $true)][int]$ProcessId
    )

    return $null -ne (Get-Process -Id $ProcessId -ErrorAction SilentlyContinue)
}

function Test-SwordSupportProcess {
    param(
        [Parameter(Mandatory = $true)][object]$ProcessDetail
    )

    $processName = [string]$ProcessDetail.ProcessName
    $commandLine = [string]$ProcessDetail.CommandLine
    if ($processName -eq "conhost") {
        return $true
    }
    if ($processName -in @("pwsh", "powershell") -and
        $commandLine.IndexOf(" -s -NoLogo -NoProfile ", [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
        return $true
    }
    return $false
}

function Get-SwordRuntimeEntries {
    $runtimeDir = Join-Path $StatusDir "runtime"
    $specs = @(
        [pscustomobject]@{
            Name = "ai_talk_core"
            Modules = @("ai_talk_core.web")
            Path = Resolve-RuntimeStatusFile `
                -EnvName "AI_TALK_CORE_RUNTIME_STATUS_FILE" `
                -DefaultPath (Join-Path $runtimeDir "ai_talk_core.json")
            StopType = "http"
            Token = [Environment]::GetEnvironmentVariable("AI_TALK_CORE_WEB_TOKEN", "Process")
            WorkingDirectory = ""
        },
        [pscustomobject]@{
            Name = "tts_service"
            Modules = @("tts_service")
            Path = Resolve-RuntimeStatusFile `
                -EnvName "TTS_SERVICE_RUNTIME_STATUS_FILE" `
                -DefaultPath (Join-Path $runtimeDir "tts_service.json")
            StopType = "http"
            Token = [Environment]::GetEnvironmentVariable("TTS_SERVICE_SHUTDOWN_TOKEN", "Process")
            WorkingDirectory = ""
        },
        [pscustomobject]@{
            Name = "mediapipe_sword_sign"
            Modules = @("mediapipe_sword_sign")
            Path = Resolve-RuntimeStatusFile `
                -EnvName "MEDIAPIPE_SWORD_SIGN_RUNTIME_STATUS_FILE" `
                -DefaultPath (Join-Path $runtimeDir "mediapipe_udp_publisher.json")
            StopType = "http"
            Token = [Environment]::GetEnvironmentVariable("MEDIAPIPE_SWORD_SIGN_CONTROL_TOKEN", "Process")
            WorkingDirectory = ""
        },
        [pscustomobject]@{
            Name = "avatar_service"
            Modules = @("avatar-service")
            Path = Resolve-RuntimeStatusFile `
                -EnvName "AVATAR_SERVICE_RUNTIME_STATUS_FILE" `
                -DefaultPath (Join-Path $runtimeDir "avatar_service.json")
            StopType = "avatar-dev-server"
            Token = ""
            WorkingDirectory = ""
        }
    )

    $avatarRootValue = [Environment]::GetEnvironmentVariable("AVATAR_SERVICE_ROOT", "Process")
    if (-not [string]::IsNullOrWhiteSpace($avatarRootValue)) {
        $avatarRoot = Resolve-SwordPath -Path $avatarRootValue -BasePath $repoRoot
        $specs[3].WorkingDirectory = $avatarRoot
    }

    $entries = @()
    foreach ($spec in $specs) {
        $status = Read-SwordRuntimeStatus -Path $spec.Path
        if ($null -eq $status) {
            continue
        }

        $module = [string]$status.module
        if ($spec.Modules -notcontains $module) {
            Write-Warning "ignore runtime status with unexpected module=$module path=$($spec.Path)"
            continue
        }

        $pidValue = 0
        try {
            $pidValue = [int]$status.pid
        }
        catch {
            $pidValue = 0
        }
        if ($pidValue -le 0) {
            continue
        }

        $detail = Get-SwordProcessDetail -ProcessId $pidValue
        if (Test-SwordProtectedProcess `
                -ProcessId $pidValue `
                -ProcessName ([string]$detail.ProcessName)) {
            Write-Warning "ignore protected runtime PID $pidValue from $($spec.Path)"
            continue
        }

        $entries += [pscustomobject]@{
            Name = $spec.Name
            Module = $module
            Path = $spec.Path
            Status = $status
            PID = $pidValue
            StopType = $spec.StopType
            Token = $spec.Token
            WorkingDirectory = $spec.WorkingDirectory
        }
    }
    return @($entries)
}

function Show-SwordCooperativeShutdownPlan {
    param(
        [object[]]$RuntimeEntries
    )

    $running = @(
        $RuntimeEntries |
            Where-Object {
                (Test-SwordRuntimeEntryRunning -Entry $_) -and
                (Test-SwordProcessAlive -ProcessId ([int]$_.PID))
            }
    )
    if ($running.Count -eq 0) {
        return
    }

    Write-Host "Cooperative shutdown methods:"
    foreach ($entry in $running) {
        $shutdownUrl = Get-SwordRuntimeEntryShutdownUrl -Entry $entry
        if ($entry.StopType -eq "http" -and
            -not [string]::IsNullOrWhiteSpace($shutdownUrl)) {
            Write-Host "  $($entry.Name): POST $shutdownUrl"
        }
        elseif ($entry.StopType -eq "avatar-dev-server") {
            Write-Host "  $($entry.Name): node scripts/dev-server.mjs stop --runtime-status-file $($entry.Path)"
        }
    }
}

function Get-SwordRuntimeEntryStatus {
    param([object]$Entry)
    if ($null -eq $Entry) {
        return $null
    }
    $property = $Entry.PSObject.Properties["Status"]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Test-SwordRuntimeEntryRunning {
    param([object]$Entry)
    $status = Get-SwordRuntimeEntryStatus -Entry $Entry
    if ($null -eq $status) {
        return $false
    }
    return ([string]$status.state) -eq "running"
}

function Get-SwordRuntimeEntryShutdownUrl {
    param([object]$Entry)
    $status = Get-SwordRuntimeEntryStatus -Entry $Entry
    if ($null -eq $status) {
        return ""
    }
    return [string]$status.shutdown_url
}

function Invoke-SwordCooperativeShutdown {
    param(
        [object[]]$RuntimeEntries
    )

    $requestedPids = @()
    foreach ($entry in $RuntimeEntries) {
        if (-not (Test-SwordRuntimeEntryRunning -Entry $entry)) {
            continue
        }
        if (-not (Test-SwordProcessAlive -ProcessId ([int]$entry.PID))) {
            continue
        }

        if ($entry.StopType -eq "http") {
            $shutdownUrl = Get-SwordRuntimeEntryShutdownUrl -Entry $entry
            if ([string]::IsNullOrWhiteSpace($shutdownUrl)) {
                continue
            }
            if (-not (Test-SwordLoopbackUrl -Url $shutdownUrl) -and
                [string]::IsNullOrWhiteSpace([string]$entry.Token)) {
                Write-Warning "skip non-loopback shutdown without token: $shutdownUrl"
                continue
            }

            $headers = @{}
            if (-not [string]::IsNullOrWhiteSpace([string]$entry.Token)) {
                $headers["Authorization"] = "Bearer $($entry.Token)"
                $headers["X-Sword-Agent-Token"] = [string]$entry.Token
            }
            $body = @{ reason = "stop_full_stack" } | ConvertTo-Json -Depth 3
            try {
                Invoke-RestMethod `
                    -Method Post `
                    -Uri $shutdownUrl `
                    -Headers $headers `
                    -ContentType "application/json" `
                    -Body $body `
                    -TimeoutSec 2 |
                    Out-Null
                Write-Host "requested cooperative shutdown: $($entry.Name) PID $($entry.PID)"
                $requestedPids += [int]$entry.PID
            }
            catch {
                Write-Warning "cooperative shutdown failed for $($entry.Name): $($_.Exception.Message)"
            }
            continue
        }

        if ($entry.StopType -eq "avatar-dev-server") {
            if ([string]::IsNullOrWhiteSpace([string]$entry.WorkingDirectory) -or
                -not (Test-Path -LiteralPath ([string]$entry.WorkingDirectory) -PathType Container)) {
                Write-Warning "avatar-service root not found; fallback stop will be used."
                continue
            }
            Push-Location ([string]$entry.WorkingDirectory)
            try {
                $avatarStopOutput = @(
                    & node scripts/dev-server.mjs stop --runtime-status-file ([string]$entry.Path) 2>&1
                )
                if ($LASTEXITCODE -eq 0) {
                    foreach ($line in $avatarStopOutput) {
                        if (-not [string]::IsNullOrWhiteSpace([string]$line)) {
                            Write-Host "  $line"
                        }
                    }
                    Write-Host "requested cooperative shutdown: $($entry.Name) PID $($entry.PID)"
                    $requestedPids += [int]$entry.PID
                }
                else {
                    foreach ($line in $avatarStopOutput) {
                        if (-not [string]::IsNullOrWhiteSpace([string]$line)) {
                            Write-Warning "avatar-service dev-server stop: $line"
                        }
                    }
                    Write-Warning "avatar-service dev-server stop exited with code $LASTEXITCODE"
                }
            }
            catch {
                Write-Warning "avatar-service dev-server stop failed: $($_.Exception.Message)"
            }
            finally {
                Pop-Location
            }
        }
    }

    return @($requestedPids | Select-Object -Unique)
}

function Wait-SwordProcessesStopped {
    param(
        [int[]]$ProcessIds,
        [int]$TimeoutSeconds = 6
    )

    $remaining = @($ProcessIds | Where-Object { $_ -and (Test-SwordProcessAlive -ProcessId $_) })
    if ($remaining.Count -eq 0) {
        return
    }

    $deadline = [DateTimeOffset]::UtcNow.AddSeconds($TimeoutSeconds)
    while ([DateTimeOffset]::UtcNow -lt $deadline) {
        $remaining = @($remaining | Where-Object { Test-SwordProcessAlive -ProcessId $_ })
        if ($remaining.Count -eq 0) {
            return
        }
        Start-Sleep -Milliseconds 250
    }

    Write-Warning "some cooperative shutdown targets are still alive: $($remaining -join ', ')"
}

$tcpPorts = @($AiTalkCorePort, [int]$TtsHttpPort, $ConsolePort, $AvatarPort)
if (-not [string]::IsNullOrWhiteSpace($MediapipeControlHttpPort)) {
    $tcpPorts += [int]$MediapipeControlHttpPort
}
if ($IncludeDify) {
    $difyBaseUrl = [Environment]::GetEnvironmentVariable("DIFY_BASE_URL", "Process")
    if (-not [string]::IsNullOrWhiteSpace($difyBaseUrl)) {
        try {
            $uri = [System.Uri]$difyBaseUrl
            if ($uri.Port -gt 0) {
                $tcpPorts += $uri.Port
            }
        }
        catch {
        }
    }
}

$portUsers = @()
$portUsers += Get-SwordPortUsers -Protocol TCP -Ports ($tcpPorts | Select-Object -Unique)
$portUsers += Get-SwordPortUsers -Protocol UDP -Ports @($GesturePort)
$runtimeEntries = Get-SwordRuntimeEntries

$needles = @(
    "start-ai-talk-core.ps1",
    "start-gesture-udp.ps1",
    "start-mediapipe-udp.ps1",
    "start-dify-watch.ps1",
    "start-tts-service.ps1",
    "start-avatar-service.ps1",
    "start-console.ps1",
    "sword_voice_agent.apps.ai_talk_core_web",
    "sword_voice_agent.apps.gesture_udp_receiver",
    "sword_voice_agent.apps.watch_handoff_to_dify",
    "sword_voice_agent.apps.console_server",
    "tts_service.apps.watch_sword_response",
    "apps/publish_udp.py",
    "apps\publish_udp.py",
    "scripts/dev-server.mjs",
    "node_modules\vite",
    "node_modules/vite"
)
if ($IncludeDify) {
    $needles += @("docker compose", "docker-compose")
}

$matchedPids = @()
$processes = @(Get-CimInstance Win32_Process -ErrorAction SilentlyContinue)
foreach ($process in $processes) {
    $commandLine = [string]$process.CommandLine
    if ([string]::IsNullOrWhiteSpace($commandLine)) {
        continue
    }
    foreach ($needle in $needles) {
        if ($commandLine.IndexOf($needle, [System.StringComparison]::OrdinalIgnoreCase) -ge 0) {
            $matchedPids += [int]$process.ProcessId
            break
        }
    }
}
$matchedPids += @($runtimeEntries | Select-Object -ExpandProperty PID)

$diagnosticPortUsers = @(
    $portUsers |
        Where-Object {
            -not (Test-SwordProtectedProcess `
                -ProcessId ([int]$_.PID) `
                -ProcessName ([string]$_.ProcessName))
        }
)
$matchedPids = @(
    $matchedPids |
        Where-Object {
            $detail = Get-SwordProcessDetail -ProcessId ([int]$_)
            -not (Test-SwordProtectedProcess `
                -ProcessId ([int]$_) `
                -ProcessName ([string]$detail.ProcessName))
        }
)

$rootPids = @(@(
    $matchedPids
) | Where-Object { $_ -and $_ -ne $PID } | Select-Object -Unique)

if ($rootPids.Count -eq 0) {
    if ($diagnosticPortUsers.Count -gt 0) {
        Write-Host "Processes are using stack ports, but they do not match Sword Voice Agent command lines. They were not selected for stopping:"
        $diagnosticPortUsers |
            Sort-Object Protocol, LocalPort, PID |
            Select-Object Protocol, LocalAddress, LocalPort, PID, ProcessName, CommandLine |
            Format-Table -AutoSize |
            Out-String -Width 240 |
            Write-Host
    }
    Write-Host "No Sword Voice Agent stack processes found."
    return
}

$targetPids = @(Get-SwordDescendantProcessIds -RootProcessIds $rootPids)
$targetPids = @($targetPids | Where-Object { $_ -and $_ -ne $PID } | Select-Object -Unique)
$targets = @(
    $targetPids |
        ForEach-Object { Get-SwordProcessDetail -ProcessId $_ } |
        Where-Object {
            $_.ProcessName -and
            -not (Test-SwordProtectedProcess `
                -ProcessId ([int]$_.PID) `
                -ProcessName ([string]$_.ProcessName))
        }
)

if ($targets.Count -eq 0) {
    Write-Host "No live target processes found."
    return
}

$supportTargets = @(
    $targets |
        Where-Object { Test-SwordSupportProcess -ProcessDetail $_ }
)
$primaryTargets = @(
    $targets |
        Where-Object { -not (Test-SwordSupportProcess -ProcessDetail $_) }
)

if ($primaryTargets.Count -eq 0) {
    Write-Host "No live application target processes found."
    if ($supportTargets.Count -gt 0) {
        Write-Host "Only support processes were detected and they were not selected for stopping."
    }
    return
}

Write-Host "Target application processes:"
$primaryTargets |
    Sort-Object PID |
    Select-Object PID, ProcessName, ParentProcessId, CommandLine |
    Format-Table -AutoSize |
    Out-String -Width 240 |
    Write-Host

if ($supportTargets.Count -gt 0) {
    $supportPids = @($supportTargets | Sort-Object PID | Select-Object -ExpandProperty PID)
    Write-Host "Support processes omitted from explicit stop: $($supportPids -join ', ')"
}

Show-SwordCooperativeShutdownPlan -RuntimeEntries $runtimeEntries

$targetPidSet = @{}
foreach ($target in $primaryTargets) {
    $targetPidSet[[int]$target.PID] = $true
}
$portOnlyUsers = @(
    $diagnosticPortUsers |
        Where-Object { -not $targetPidSet.ContainsKey([int]$_.PID) }
)
if ($portOnlyUsers.Count -gt 0) {
    Write-Host "Port users below are diagnostic only and will not be stopped because their command lines do not match this stack:"
    $portOnlyUsers |
        Sort-Object Protocol, LocalPort, PID |
        Select-Object Protocol, LocalAddress, LocalPort, PID, ProcessName, CommandLine |
        Format-Table -AutoSize |
        Out-String -Width 240 |
        Write-Host
}

if ($DryRun) {
    Write-Host "[dry-run] no shutdown requests or process stops were performed."
    return
}

if (-not $Force) {
    $answer = Read-Host "Stop these processes? [y/N]"
    if ($answer -notmatch "^(y|yes)$") {
        Write-Host "Canceled."
        return
    }
}

$cooperativePids = Invoke-SwordCooperativeShutdown -RuntimeEntries $runtimeEntries
Wait-SwordProcessesStopped -ProcessIds $cooperativePids

$remainingTargets = @(
    $primaryTargets |
        Where-Object { Test-SwordProcessAlive -ProcessId ([int]$_.PID) }
)
if ($remainingTargets.Count -eq 0) {
    Write-Host "All target processes stopped cooperatively."
    return
}

foreach ($target in @($remainingTargets | Sort-Object PID -Descending)) {
    try {
        Stop-Process -Id $target.PID -Force -ErrorAction Stop
        Write-Host "stopped PID $($target.PID) $($target.ProcessName)"
    }
    catch {
        Write-Warning "failed to stop PID $($target.PID): $($_.Exception.Message)"
    }
}
