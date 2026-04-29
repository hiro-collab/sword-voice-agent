param(
    [string]$EnvPath = ".env",
    [string]$HostName = "127.0.0.1",
    [int]$Port = 8765,
    [int]$DebugEvery = 30,
    [string]$StatusDir = ".cache\sword_voice_agent",
    [string]$ModelPath = "",
    [string]$ModelSha256 = "",
    [switch]$AllowUntrustedModel,
    [switch]$SkipModelPrecheck,
    [switch]$PrecheckOnly,
    [string]$HeartbeatEvery = "",
    [string]$LatencyProfile = "",
    [string]$StateEvery = "",
    [string]$RuntimeStatusFile = "",
    [string]$ControlHttpHost = "",
    [string]$ControlHttpPort = "",
    [string]$ControlToken = "",
    [switch]$EdgeOnly,
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
if ($PrecheckOnly -and $SkipModelPrecheck) {
    throw "-PrecheckOnly cannot be used with -SkipModelPrecheck."
}

if ([string]::IsNullOrWhiteSpace($ModelPath)) {
    $ModelPath = [Environment]::GetEnvironmentVariable(
        "MEDIAPIPE_SWORD_SIGN_MODEL_PATH",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($ModelPath)) {
    $ModelPath = "gesture_model.pkl"
}
if ([string]::IsNullOrWhiteSpace($ModelSha256)) {
    $ModelSha256 = [Environment]::GetEnvironmentVariable(
        "MEDIAPIPE_SWORD_SIGN_MODEL_SHA256",
        "Process"
    )
}
if (-not $AllowUntrustedModel) {
    $allowUntrusted = [Environment]::GetEnvironmentVariable(
        "MEDIAPIPE_SWORD_SIGN_ALLOW_UNTRUSTED_MODEL",
        "Process"
    )
    if ($allowUntrusted -match "^(1|true|yes|on)$") {
        $AllowUntrustedModel = $true
    }
}
if ([string]::IsNullOrWhiteSpace($HeartbeatEvery)) {
    $HeartbeatEvery = [Environment]::GetEnvironmentVariable(
        "MEDIAPIPE_SWORD_SIGN_HEARTBEAT_EVERY",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($LatencyProfile)) {
    $LatencyProfile = [Environment]::GetEnvironmentVariable(
        "MEDIAPIPE_SWORD_SIGN_LATENCY_PROFILE",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($StateEvery)) {
    $StateEvery = [Environment]::GetEnvironmentVariable(
        "MEDIAPIPE_SWORD_SIGN_STATE_EVERY",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($RuntimeStatusFile)) {
    $RuntimeStatusFile = [Environment]::GetEnvironmentVariable(
        "MEDIAPIPE_SWORD_SIGN_RUNTIME_STATUS_FILE",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($ControlHttpHost)) {
    $ControlHttpHost = [Environment]::GetEnvironmentVariable(
        "MEDIAPIPE_SWORD_SIGN_CONTROL_HTTP_HOST",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($ControlHttpPort)) {
    $ControlHttpPort = [Environment]::GetEnvironmentVariable(
        "MEDIAPIPE_SWORD_SIGN_CONTROL_HTTP_PORT",
        "Process"
    )
}
if ([string]::IsNullOrWhiteSpace($ControlToken)) {
    $ControlToken = [Environment]::GetEnvironmentVariable(
        "MEDIAPIPE_SWORD_SIGN_CONTROL_TOKEN",
        "Process"
    )
}
if (-not $EdgeOnly) {
    $edgeOnlyValue = [Environment]::GetEnvironmentVariable(
        "MEDIAPIPE_SWORD_SIGN_EDGE_ONLY",
        "Process"
    )
    if ($edgeOnlyValue -match "^(1|true|yes|on)$") {
        $EdgeOnly = $true
    }
}
if ([string]::IsNullOrWhiteSpace($HeartbeatEvery)) {
    $HeartbeatEvery = "1s"
}
if ([string]::IsNullOrWhiteSpace($RuntimeStatusFile)) {
    $RuntimeStatusFile = Join-Path $StatusDir "runtime\mediapipe_udp_publisher.json"
}
if ([string]::IsNullOrWhiteSpace($ControlHttpHost)) {
    $ControlHttpHost = "127.0.0.1"
}
if ([string]::IsNullOrWhiteSpace($ControlHttpPort)) {
    $ControlHttpPort = "18765"
}
if (-not $DryRun -and -not $PrecheckOnly) {
    Assert-SwordPortsAvailable -TcpPorts @([int]$ControlHttpPort)
}

$resolvedModelPath = Resolve-SwordPath -Path $ModelPath -BasePath $mediapipeRoot
$resolvedRuntimeStatusFile = Resolve-SwordPath -Path $RuntimeStatusFile -BasePath (Get-SwordRepoRoot)
$modelHash = ""
if (-not $DryRun -and -not $SkipModelPrecheck) {
    Write-Host "[mediapipe model precheck]"
    Write-Host "path: $resolvedModelPath"
    if (-not (Test-Path -LiteralPath $resolvedModelPath -PathType Leaf)) {
        throw (
            "gesture model not found: $resolvedModelPath. " +
            "Create gesture_model.pkl in MEDIAPIPE_SWORD_SIGN_ROOT, or set " +
            "MEDIAPIPE_SWORD_SIGN_MODEL_PATH."
        )
    }
    $file = Get-Item -LiteralPath $resolvedModelPath
    $modelHash = (Get-FileHash -LiteralPath $resolvedModelPath -Algorithm SHA256).Hash.ToLowerInvariant()
    Write-Host ("size: {0} bytes" -f $file.Length)
    Write-Host "sha256: $modelHash"
    if (-not [string]::IsNullOrWhiteSpace($ModelSha256) -and
        $modelHash -ne $ModelSha256.ToLowerInvariant()) {
        throw "gesture model SHA-256 mismatch. expected=$ModelSha256 actual=$modelHash"
    }

    $healthArgs = @(
        "run",
        "python",
        "apps/publish_udp.py",
        "--health-json",
        "--host",
        $HostName,
        "--port",
        [string]$Port,
        "--model-path",
        $resolvedModelPath,
        "--suppress-protobuf-warnings"
    )
    if (-not [string]::IsNullOrWhiteSpace($ModelSha256)) {
        $healthArgs += @("--model-sha256", $ModelSha256)
    }
    if ($AllowUntrustedModel) {
        $healthArgs += "--allow-untrusted-model"
    }
    Push-Location $mediapipeRoot
    try {
        $healthOutput = & uv @healthArgs
        $healthExitCode = $LASTEXITCODE
    }
    finally {
        Pop-Location
    }
    $healthJson = ($healthOutput -join "`n").Trim()
    if ([string]::IsNullOrWhiteSpace($healthJson)) {
        throw "MediaPipe health check returned no JSON output."
    }
    try {
        $health = $healthJson | ConvertFrom-Json
    }
    catch {
        throw "MediaPipe health check returned invalid JSON: $healthJson"
    }
    if (-not $health.model.available) {
        throw "gesture model load failed: $($health.model.error)"
    }
    Write-Host "model_load: ok"
    if (-not $health.camera.available) {
        throw "camera not available: index $($health.camera.selected_index)"
    }
    Write-Host "camera: ok index=$($health.camera.selected_index)"
    if ($healthExitCode -ne 0) {
        throw "MediaPipe health check failed with exit code $healthExitCode."
    }
if ($PrecheckOnly) {
        Write-Host "precheck_only: ok"
        return
    }
}
elseif ($DryRun) {
    Write-Host "[dry-run] MediaPipe model precheck will use: $resolvedModelPath"
}

$command = @(
    "uv",
    "run",
    "python",
    "apps/publish_udp.py",
    "--host",
    $HostName,
    "--port",
    [string]$Port,
    "--model-path",
    $resolvedModelPath,
    "--debug",
    "--debug-every",
    [string]$DebugEvery,
    "--heartbeat-every",
    $HeartbeatEvery,
    "--runtime-status-file",
    $resolvedRuntimeStatusFile,
    "--control-http-host",
    $ControlHttpHost,
    "--control-http-port",
    $ControlHttpPort
)

if (-not [string]::IsNullOrWhiteSpace($ModelSha256)) {
    $command += @("--model-sha256", $ModelSha256)
}
if ($AllowUntrustedModel) {
    $command += "--allow-untrusted-model"
}
if ($Preview) {
    $command += "--preview"
}
if ($SuppressProtobufWarnings) {
    $command += "--suppress-protobuf-warnings"
}
if (-not [string]::IsNullOrWhiteSpace($LatencyProfile)) {
    $command += @("--latency-profile", $LatencyProfile)
}
if (-not [string]::IsNullOrWhiteSpace($StateEvery)) {
    $command += @("--state-every", $StateEvery)
}
if ($EdgeOnly) {
    $command += "--edge-only"
}
if (-not [string]::IsNullOrWhiteSpace($ControlToken)) {
    $command += @("--control-token", $ControlToken)
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
    -Detail "$HostName`:$Port model=$(Split-Path -Leaf $resolvedModelPath)" `
    -DryRun:$DryRun
