param(
    [string]$HostName = "127.0.0.1",
    [int]$Port = 18787,
    [string]$StatusDir = ".cache\sword_voice_agent",
    [string]$EnvPath = ".env",
    [switch]$SkipEnvImport,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
$fixedFailureClasses = @(
    "thought_core_controller_cleanup_incomplete",
    "thought_core_controller_exited",
    "thought_core_controller_manifest_invalid",
    "thought_core_controller_manifest_write_failed",
    "thought_core_controller_start_failed"
)
trap {
    $candidate = [string]$_.Exception.Message
    $failureClass = if ($fixedFailureClasses -ccontains $candidate) {
        $candidate
    }
    else {
        "thought_core_controller_start_failed"
    }
    [Console]::Error.WriteLine($failureClass)
    exit 1
}
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
$resolvedEnvPath = Resolve-SwordPath -Path $EnvPath
if ((-not $SkipEnvImport) -and (Test-Path -LiteralPath $resolvedEnvPath -PathType Leaf)) {
    Import-SwordEnv -EnvPath $resolvedEnvPath 6>$null
}
$canonicalBrokerEnvironment = @{}
$isBrokerPrimary = $SkipEnvImport -and $env:THOUGHT_CORE_LLM_PROVIDER -ceq "sword-openai-broker"
if ($isBrokerPrimary) {
    foreach ($name in @(
        "THOUGHT_CORE_LLM_ENABLED",
        "THOUGHT_CORE_LLM_PROVIDER",
        "THOUGHT_CORE_LLM_BASE_URL",
        "THOUGHT_CORE_LLM_MODEL",
        "THOUGHT_CORE_LLM_TIMEOUT_S",
        "THOUGHT_CORE_ACTION_LLM_ENABLED"
    )) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            $canonicalBrokerEnvironment[$name] = $value
        }
    }
    $canonicalBrokerEnvironment["THOUGHT_CORE_ACTION_LLM_ENABLED"] = "0"
    foreach ($name in @(
        "OPENAI_API_KEY",
        "OPENAI_BASE_URL",
        "OPENAI_MODEL",
        "THOUGHT_CORE_LLM_API_KEY",
        "THOUGHT_CORE_LLM_PROVIDER",
        "THOUGHT_CORE_LLM_ADAPTER",
        "THOUGHT_CORE_LLM_BASE_URL",
        "THOUGHT_CORE_LLM_MODEL",
        "THOUGHT_CORE_LLM_TIMEOUT_S",
        "THOUGHT_CORE_LLM_ENABLED",
        "THOUGHT_CORE_ACTION_LLM_API_KEY",
        "THOUGHT_CORE_ACTION_LLM_PROVIDER",
        "THOUGHT_CORE_ACTION_LLM_ADAPTER",
        "THOUGHT_CORE_ACTION_LLM_BASE_URL",
        "THOUGHT_CORE_ACTION_LLM_MODEL",
        "THOUGHT_CORE_ACTION_LLM_TIMEOUT_S",
        "THOUGHT_CORE_ACTION_LLM_ENABLED"
    )) {
        Remove-Item -Path "Env:$name" -ErrorAction SilentlyContinue
    }
    foreach ($name in $canonicalBrokerEnvironment.Keys) {
        Set-Item -Path "Env:$name" -Value $canonicalBrokerEnvironment[$name]
    }
}
if ($env:THOUGHT_CORE_FORCE_NO_PROVIDER -match "^(1|true|yes|on)$") {
    $env:THOUGHT_CORE_LLM_ENABLED = "0"
    $env:THOUGHT_CORE_ACTION_LLM_ENABLED = "0"
    foreach ($name in @(
        "THOUGHT_CORE_LLM_BASE_URL",
        "THOUGHT_CORE_LLM_API_KEY",
        "THOUGHT_CORE_LLM_MODEL",
        "OPENAI_BASE_URL",
        "OPENAI_API_KEY",
        "OPENAI_MODEL"
    )) {
        Set-Item -Path "Env:$name" -Value ""
    }
}
if ([string]::IsNullOrWhiteSpace($env:THOUGHT_CORE_PERSONA) -and [string]::IsNullOrWhiteSpace($env:SWORD_THOUGHT_CORE_PERSONA)) {
    $env:THOUGHT_CORE_PERSONA = "cheerful_ossan"
}
$thoughtCorePath = Join-Path $repoRoot "services\thought-core"
if (-not (Test-Path -LiteralPath $thoughtCorePath -PathType Container)) {
    throw "thought-core service directory not found: $thoughtCorePath"
}
$thoughtCoreSrcPath = Join-Path $thoughtCorePath "src"
if (-not (Test-Path -LiteralPath $thoughtCoreSrcPath -PathType Container)) {
    throw "thought-core src directory not found: $thoughtCoreSrcPath"
}

$env:PYTHONPATH = $thoughtCoreSrcPath
$controllerManifestPath = [string]$env:SWORD_THOUGHT_CORE_CONTROLLER_MANIFEST
$controllerLaunchNonce = [string]$env:SWORD_THOUGHT_CORE_LAUNCH_NONCE
if (
    [string]::IsNullOrWhiteSpace($controllerManifestPath) -ne
    [string]::IsNullOrWhiteSpace($controllerLaunchNonce)
) {
    throw "thought_core_controller_manifest_invalid"
}
if (-not [string]::IsNullOrWhiteSpace($controllerManifestPath)) {
    $expectedManifestPath = [IO.Path]::GetFullPath((Join-Path (Resolve-SwordPath -Path $StatusDir) "controller.json"))
    if (
        [IO.Path]::GetFullPath($controllerManifestPath) -cne $expectedManifestPath -or
        $controllerLaunchNonce -notmatch "^[a-f0-9]{32}$"
    ) {
        throw "thought_core_controller_manifest_invalid"
    }
}
Remove-Item Env:SWORD_THOUGHT_CORE_CONTROLLER_MANIFEST -ErrorAction SilentlyContinue
Remove-Item Env:SWORD_THOUGHT_CORE_LAUNCH_NONCE -ErrorAction SilentlyContinue

$controllerExecutable = "uv"
$controllerPrefix = @("run", "python")
if (
    $env:NODE_ENV -eq "test" -and
    -not [string]::IsNullOrWhiteSpace($env:HOME_CONTROL_STACK_TEST_THOUGHT_CORE_PYTHON)
) {
    $controllerExecutable = [string]$env:HOME_CONTROL_STACK_TEST_THOUGHT_CORE_PYTHON
    $controllerPrefix = @([string]$env:HOME_CONTROL_STACK_TEST_THOUGHT_CORE_CONTROLLER_SCRIPT)
}
$command = @($controllerExecutable)
$command += @($controllerPrefix)
$command += @(
    "-m",
    "thought_core",
    "--host",
    $HostName,
    "--port",
    [string]$Port
)

Invoke-WithModuleStatus `
    -WorkingDirectory $repoRoot `
    -Command $command `
    -StatusDir $StatusDir `
    -ModuleName "thought_core_api" `
    -ModuleLabel "thought-core API" `
    -Detail ("http://{0}:{1}" -f $HostName, $Port) `
    -ControllerManifestPath $controllerManifestPath `
    -ControllerLaunchNonce $controllerLaunchNonce `
    -ControllerServiceClass "thought_core_api" `
    -ControllerExpectedModule "thought_core" `
    -ControllerExpectedPort $Port `
    -DryRun:$DryRun
