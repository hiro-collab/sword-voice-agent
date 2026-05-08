param(
    [switch]$Speak,
    [switch]$NoRestart,
    [switch]$NoOpenBrowser,
    [switch]$KeepFaultConfig,
    [switch]$AllowPersistentFaultMode,
    [switch]$AllowExternalDify,
    [switch]$NoFinalRestart,
    [string]$ClientId = "sword-local",
    [int]$StartupWaitSeconds = 8,
    [int]$DelayBetweenCasesSeconds = 8,
    [string]$WorkspaceRoot = "",
    [string]$StackStateDir = ""
)

Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

function Resolve-StackStateDir {
    param(
        [Parameter(Mandatory = $true)][string]$WorkspaceRoot,
        [string]$StackStateDir = ""
    )
    if ([string]::IsNullOrWhiteSpace($StackStateDir)) {
        $StackStateDir = [Environment]::GetEnvironmentVariable("HOME_CONTROL_STACK_STATE_DIR")
    }
    if ([string]::IsNullOrWhiteSpace($StackStateDir)) {
        return Join-Path $WorkspaceRoot ".cache\home-control-stack"
    }
    if ([System.IO.Path]::IsPathRooted($StackStateDir)) {
        return $StackStateDir
    }
    return Join-Path $WorkspaceRoot $StackStateDir
}

. (Join-Path $PSScriptRoot "resolve-home-control-workspace.ps1")
$WorkspaceRoot = Resolve-HomeControlWorkspaceRoot -WorkspaceRoot $WorkspaceRoot -ScriptRoot $PSScriptRoot
$StackStateDir = Resolve-StackStateDir -WorkspaceRoot $WorkspaceRoot -StackStateDir $StackStateDir

$HomeAssistantRoot = Join-Path $WorkspaceRoot "organs\action\home-assistant-server"
$HomeAssistantConfigPath = Join-Path $HomeAssistantRoot "config\home-control.yaml"
$AituberEnvPath = Join-Path $WorkspaceRoot "organs\expression\aituber-kit\.env"
$CacheDir = Join-Path $StackStateDir "fault-e2e"
$LogDir = Join-Path $StackStateDir "logs"
$StartScript = Join-Path $PSScriptRoot "start-home-control-stack.ps1"
$RunStamp = Get-Date -Format "yyyyMMdd-HHmmss"
$script:BrowserOpenedForFaultE2e = $false

function Write-Step {
    param([string]$Message)
    Write-Host "[fault-e2e] $Message"
}

function Read-Utf8File {
    param([string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    return [System.IO.File]::ReadAllText($Path, [System.Text.Encoding]::UTF8)
}

function Write-Utf8File {
    param(
        [string]$Path,
        [string]$Text
    )
    $encoding = New-Object System.Text.UTF8Encoding($false)
    [System.IO.File]::WriteAllText($Path, $Text, $encoding)
}

function Set-FaultBlock {
    param(
        [string]$ConfigText,
        [string]$FaultYaml
    )
    $base = [regex]::Replace($ConfigText, "(?ms)\r?\n?^faults:\r?\n.*\z", "")
    return ($base.TrimEnd() + "`r`n`r`n" + $FaultYaml.TrimEnd() + "`r`n")
}

function Get-PhaseAFaultYaml {
    @"
faults:
  enabled: true
  enabled_env: "HOME_CONTROL_FAULT_MODE"
  rules:
    - match:
        source: "dify"
        action_id: "light_on"
      scenario: "always_success"
      message: "simulated always success"
    - match:
        source: "dify"
        action_id: "light_off"
      scenario: "fail_once_then_success"
      message: "simulated transient failure once"
    - match:
        source: "dify"
        action_id: "fan_on"
      scenario: "fail_twice_then_success"
      message: "simulated transient failure twice"
    - match:
        source: "dify"
        action_id: "fan_off"
      scenario: "fail_always"
      message: "simulated persistent failure"
"@
}

function Get-PhaseBFaultYaml {
    @"
faults:
  enabled: true
  enabled_env: "HOME_CONTROL_FAULT_MODE"
  rules:
    - match:
        source: "dify"
        action_id: "light_on"
      scenario: "confirmation_required"
      message: "simulated confirmation required"
    - match:
        source: "dify"
        action_id: "light_off"
      scenario: "timeout_once"
      message: "simulated timeout once"
    - match:
        source: "dify"
        action_id: "fan_on"
      scenario: "duplicate"
      message: "simulated duplicate"
    - match:
        source: "dify"
        action_id: "fan_off"
      scenario: "unsupported_action"
      message: "simulated unsupported action"
"@
}

function Wait-HttpReady {
    param(
        [string]$Url,
        [int]$Attempts = 30
    )
    for ($i = 0; $i -lt $Attempts; $i++) {
        try {
            Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec 5 | Out-Null
            return
        }
        catch {
            Start-Sleep -Seconds 1
        }
    }
    throw "Timed out waiting for $Url"
}

function Get-DotEnvValue {
    param(
        [string]$Path,
        [string]$Name
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    foreach ($line in Get-Content -LiteralPath $Path) {
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)\s*$") {
            return $Matches[1].Trim().Trim('"').Trim("'")
        }
    }
    return ""
}

function Test-LoopbackUrl {
    param([string]$Url)
    if ([string]::IsNullOrWhiteSpace($Url)) {
        return $true
    }
    try {
        $uri = [uri]$Url
    }
    catch {
        throw "Invalid Dify URL in AITuber env: $Url"
    }
    return @("localhost", "127.0.0.1", "::1") -contains $uri.Host -or $uri.Host.StartsWith("127.")
}

function Assert-LocalDifyTarget {
    if ($AllowExternalDify) {
        Write-Step "external Dify target allowed by -AllowExternalDify"
        return
    }
    $difyUrl = Get-DotEnvValue -Path $AituberEnvPath -Name "DIFY_API_URL"
    if ([string]::IsNullOrWhiteSpace($difyUrl)) {
        $difyUrl = Get-DotEnvValue -Path $AituberEnvPath -Name "DIFY_URL"
    }
    if (-not (Test-LoopbackUrl -Url $difyUrl)) {
        throw "Refusing to run fault E2E against non-loopback Dify URL. Use -AllowExternalDify only if this is intentional."
    }
}

function New-PhaseConfig {
    param(
        [string]$Phase,
        [string]$BaseConfigText,
        [string]$FaultYaml
    )
    $path = Join-Path $CacheDir "fault-e2e-$RunStamp-$Phase-home-control.yaml"
    Write-Utf8File -Path $path -Text (Set-FaultBlock -ConfigText $BaseConfigText -FaultYaml $FaultYaml)
    return $path
}

function Remove-GeneratedArtifact {
    param(
        [string]$Path,
        [string]$FileNamePattern
    )
    if ([string]::IsNullOrWhiteSpace($Path)) {
        return
    }

    $cacheFullPath = [System.IO.Path]::GetFullPath($CacheDir).TrimEnd("\", "/")
    $artifactFullPath = [System.IO.Path]::GetFullPath($Path)
    $cachePrefix = $cacheFullPath + [System.IO.Path]::DirectorySeparatorChar
    if (-not $artifactFullPath.StartsWith($cachePrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to remove generated artifact outside cache dir: $Path"
    }

    $fileName = [System.IO.Path]::GetFileName($artifactFullPath)
    if ($fileName -notlike $FileNamePattern) {
        throw "Refusing to remove unexpected generated artifact: $Path"
    }

    Remove-Item -LiteralPath $artifactFullPath -Force -ErrorAction SilentlyContinue
}

function Start-HomeControlStackForTest {
    param(
        [string]$Phase,
        [string]$ConfigPath = ""
    )
    if ($NoRestart) {
        Write-Step "skip stack restart for phase $Phase"
        return
    }

    New-Item -ItemType Directory -Force -Path $LogDir | Out-Null
    $outLog = Join-Path $LogDir "fault-e2e-$RunStamp-$Phase.out.log"
    $errLog = Join-Path $LogDir "fault-e2e-$RunStamp-$Phase.err.log"

    Write-Step "restart stack for phase $Phase"
    $arguments = @(
        "-NoProfile",
        "-ExecutionPolicy",
        "Bypass",
        "-File",
        $StartScript,
        "-StopExisting",
        "-StackStateDir",
        $StackStateDir
    )
    if (-not [string]::IsNullOrWhiteSpace($ConfigPath)) {
        $arguments += @("-HomeControlConfigPath", $ConfigPath, "-EnableHomeControlFaultInjection")
    }
    Start-Process `
        -FilePath "powershell.exe" `
        -ArgumentList $arguments `
        -WorkingDirectory $WorkspaceRoot `
        -WindowStyle Hidden `
        -RedirectStandardOutput $outLog `
        -RedirectStandardError $errLog | Out-Null

    Start-Sleep -Seconds $StartupWaitSeconds
    Wait-HttpReady -Url "http://127.0.0.1:8787/health"
    Wait-HttpReady -Url "http://127.0.0.1:3000/"

    if ($Speak -and -not $NoOpenBrowser -and -not $script:BrowserOpenedForFaultE2e) {
        Start-Process "http://127.0.0.1:3000/"
        Start-Process "http://127.0.0.1:3000/projection-visual"
        $script:BrowserOpenedForFaultE2e = $true
    }
}

function Get-ExpectedJson {
    param([string[]]$Values)
    $escaped = $Values | ForEach-Object { '"' + ($_ -replace '\\', '\\' -replace '"', '\"') + '"' }
    return "[" + ($escaped -join ",") + "]"
}

function Write-NodeRunner {
    param([string]$Path)
    $code = @'
import fs from 'node:fs';

const workspaceRoot = process.env.TEST_WORKSPACE_ROOT;
const caseName = process.env.TEST_CASE;
const query = process.env.TEST_QUERY;
const clientId = process.env.TEST_CLIENT_ID || 'sword-local';
const speak = process.env.TEST_SPEAK === '1';
const resultPath = process.env.TEST_RESULT_PATH;
const expectedStatuses = JSON.parse(process.env.TEST_EXPECTED_STATUSES || '[]');
const expectedStatusSequences = JSON.parse(process.env.TEST_EXPECTED_STATUS_SEQUENCES || '[]');
const startedMs = Date.now() - 2000;
const homeEventsPath = `${workspaceRoot}/organs/action/home-assistant-server/.cache/home_control/events.jsonl`.replaceAll('\\', '/');

if (!resultPath) {
  throw new Error('TEST_RESULT_PATH is required');
}

const postSpeech = async (text) => {
  const clean = text.trim();
  if (!speak || !clean) return;
  const response = await fetch(`http://127.0.0.1:3000/api/messages?clientId=${encodeURIComponent(clientId)}&type=direct_send`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages: [clean] }),
  });
  if (!response.ok) {
    throw new Error(`direct_send ${response.status}: ${await response.text()}`);
  }
};

const extractSegments = (state, final = false) => {
  const segments = [];
  while (true) {
    const match = state.buffer.match(/[。！？!?]/);
    if (!match) break;
    const end = match.index + match[0].length;
    const segment = state.buffer.slice(0, end).trim();
    state.buffer = state.buffer.slice(end);
    if (segment) segments.push(segment);
  }
  if (final && state.buffer.trim()) {
    segments.push(state.buffer.trim());
    state.buffer = '';
  }
  return segments;
};

const readHomeEvents = () => {
  if (!fs.existsSync(homeEventsPath)) return [];
  return fs.readFileSync(homeEventsPath, 'utf8')
    .trim()
    .split(/\r?\n/)
    .filter(Boolean)
    .map((line) => {
      try {
        return JSON.parse(line);
      } catch {
        return null;
      }
    })
    .filter((entry) =>
      entry &&
      entry.event === 'fault_injected' &&
      entry.scenario === caseName &&
      Date.parse(entry.timestamp) >= startedMs
    );
};

const response = await fetch('http://127.0.0.1:3000/api/difyChat/', {
  method: 'POST',
  headers: { 'Content-Type': 'application/json' },
  body: JSON.stringify({ query, stream: true, conversationId: '' }),
});

if (!response.ok) {
  throw new Error(`difyChat ${response.status}: ${await response.text()}`);
}
if (!response.body) {
  throw new Error('difyChat response body is empty');
}

const reader = response.body.getReader();
const decoder = new TextDecoder();
let sseBuffer = '';
let answer = '';
const speech = { buffer: '' };
const spoken = [];

const handleText = async (text) => {
  sseBuffer += text;
  const lines = sseBuffer.split('\n');
  sseBuffer = lines.pop() || '';
  for (const rawLine of lines) {
    const line = rawLine.trim();
    if (!line.startsWith('data:')) continue;
    const jsonText = line.slice(5).trim();
    if (!jsonText || jsonText === '[DONE]') continue;

    let data;
    try {
      data = JSON.parse(jsonText);
    } catch {
      continue;
    }

    if ((data.event === 'agent_message' || data.event === 'message') && typeof data.answer === 'string') {
      answer += data.answer;
      speech.buffer += data.answer;
      for (const segment of extractSegments(speech)) {
        spoken.push(segment);
        await postSpeech(segment);
      }
    }
  }
};

while (true) {
  const { done, value } = await reader.read();
  if (done) break;
  await handleText(decoder.decode(value, { stream: true }));
}
await handleText('\n');
for (const segment of extractSegments(speech, true)) {
  spoken.push(segment);
  await postSpeech(segment);
}

await new Promise((resolve) => setTimeout(resolve, 1500));
const homeEvents = readHomeEvents();
const statuses = homeEvents.map((event) => event.status);
const attempts = homeEvents.map((event) => event.attempt);
const statusSequence = statuses.join(',');
const acceptableSequences = expectedStatusSequences.length
  ? expectedStatusSequences
  : [expectedStatuses.join(',')];
const statusOk = acceptableSequences.includes(statusSequence);

const result = {
  case: caseName,
  query,
  stream_completed: true,
  status_ok: statusOk,
  expected_statuses: expectedStatuses,
  expected_status_sequences: acceptableSequences,
  statuses,
  attempts,
  request_ids: homeEvents.map((event) => event.request_id),
  answer_preview: answer.slice(0, 220),
  spoken,
};

fs.writeFileSync(resultPath, JSON.stringify(result), 'utf8');
console.log(JSON.stringify(result));
'@
    Write-Utf8File -Path $Path -Text $code
}

function Invoke-FaultCase {
    param([pscustomobject]$Case)
    $env:TEST_WORKSPACE_ROOT = $WorkspaceRoot
    $env:TEST_CLIENT_ID = $ClientId
    $env:TEST_SPEAK = if ($Speak) { "1" } else { "0" }
    $env:TEST_CASE = $Case.Scenario
    $env:TEST_QUERY = ("RUN" + (Get-Date -Format "HHmmss") + " " + $Case.Utterance)
    $env:TEST_EXPECTED_STATUSES = Get-ExpectedJson -Values $Case.ExpectedStatuses
    $allowedStatusSequences = @($Case.ExpectedStatuses -join ",")
    if ($Case.PSObject.Properties.Name -contains "AllowedStatusSequences") {
        $allowedStatusSequences = @($Case.AllowedStatusSequences)
    }
    $env:TEST_EXPECTED_STATUS_SEQUENCES = Get-ExpectedJson -Values $allowedStatusSequences
    $env:TEST_RESULT_PATH = Join-Path $CacheDir ("fault-e2e-$RunStamp-$($Case.Scenario)-result.json")

    Write-Step "case $($Case.Scenario): $env:TEST_QUERY"
    try {
        $output = & node $script:NodeRunnerPath 2>&1
        if ($LASTEXITCODE -ne 0) {
            throw "node runner failed for $($Case.Scenario):`n$output"
        }
        if (-not (Test-Path -LiteralPath $env:TEST_RESULT_PATH -PathType Leaf)) {
            throw "node runner did not write result file for $($Case.Scenario):`n$output"
        }
        $resultJson = Read-Utf8File -Path $env:TEST_RESULT_PATH
        try {
            $result = $resultJson | ConvertFrom-Json
        }
        catch {
            throw "failed to parse result file for $($Case.Scenario): $($_.Exception.Message)`nstdout/stderr:`n$output`nresult:`n$resultJson"
        }
    }
    finally {
        Remove-GeneratedArtifact -Path $env:TEST_RESULT_PATH -FileNamePattern "fault-e2e-$RunStamp-*-result.json"
    }
    $mark = if ($result.status_ok) { "OK" } else { "NG" }
    Write-Host ("[{0}] {1} statuses={2} expected={3}" -f $mark, $result.case, (($result.statuses) -join ","), (($result.expected_status_sequences) -join "|"))
    return $result
}

function ConvertFrom-CodePoints {
    param([int[]]$CodePoints)
    return (-join ($CodePoints | ForEach-Object { [char]$_ }))
}

if (-not (Test-Path -LiteralPath $StartScript -PathType Leaf)) {
    throw "Start script not found: $StartScript"
}
if (-not (Get-Command node -ErrorAction SilentlyContinue)) {
    throw "node command is required for streaming SSE tests."
}
if ($ClientId -notmatch '^[A-Za-z0-9._-]{1,80}$') {
    throw "ClientId must be 1-80 characters and contain only letters, numbers, dot, underscore, or hyphen."
}
if ($KeepFaultConfig -and -not $AllowPersistentFaultMode) {
    throw "-KeepFaultConfig leaves fault injection running. Re-run with -AllowPersistentFaultMode only for an intentional isolated test session."
}
if ($NoFinalRestart -and -not $AllowPersistentFaultMode) {
    throw "-NoFinalRestart can leave a fault-mode stack running. Re-run with -AllowPersistentFaultMode only for an intentional isolated test session."
}
if ($NoFinalRestart -and -not $KeepFaultConfig) {
    throw "-NoFinalRestart requires -KeepFaultConfig so the running fault-mode stack does not point at a deleted temporary config."
}
Assert-LocalDifyTarget

New-Item -ItemType Directory -Force -Path $CacheDir | Out-Null
$script:NodeRunnerPath = Join-Path $CacheDir "fault-e2e-$RunStamp-runner.mjs"
Write-NodeRunner -Path $script:NodeRunnerPath

$originalConfigText = Read-Utf8File -Path $HomeAssistantConfigPath
$results = New-Object System.Collections.Generic.List[object]
$failed = $false
$runError = $null
$generatedConfigPaths = New-Object System.Collections.Generic.List[string]

$utterLightOn = ConvertFrom-CodePoints @(0x30E9, 0x30A4, 0x30C8, 0x3092, 0x3064, 0x3051, 0x3066)
$utterLightOff = ConvertFrom-CodePoints @(0x96FB, 0x6C17, 0x3092, 0x6D88, 0x3057, 0x3066)
$utterFanOn = ConvertFrom-CodePoints @(0x6247, 0x98A8, 0x6A5F, 0x3092, 0x3064, 0x3051, 0x3066)
$utterFanOff = ConvertFrom-CodePoints @(0x6247, 0x98A8, 0x6A5F, 0x3092, 0x6D88, 0x3057, 0x3066)

$phaseACases = @(
    [pscustomobject]@{ Scenario = "always_success"; Utterance = $utterLightOn; ExpectedStatuses = @("submitted") },
    [pscustomobject]@{ Scenario = "fail_once_then_success"; Utterance = $utterLightOff; ExpectedStatuses = @("failed", "submitted") },
    [pscustomobject]@{ Scenario = "fail_twice_then_success"; Utterance = $utterFanOn; ExpectedStatuses = @("failed", "failed", "submitted") },
    [pscustomobject]@{ Scenario = "fail_always"; Utterance = $utterFanOff; ExpectedStatuses = @("failed"); AllowedStatusSequences = @("failed", "failed,failed", "failed,failed,failed") }
)
$phaseBCases = @(
    [pscustomobject]@{ Scenario = "confirmation_required"; Utterance = $utterLightOn; ExpectedStatuses = @("confirmation_required") },
    [pscustomobject]@{ Scenario = "timeout_once"; Utterance = $utterLightOff; ExpectedStatuses = @("failed", "submitted"); AllowedStatusSequences = @("failed", "failed,submitted") },
    [pscustomobject]@{ Scenario = "duplicate"; Utterance = $utterFanOn; ExpectedStatuses = @("duplicate") },
    [pscustomobject]@{ Scenario = "unsupported_action"; Utterance = $utterFanOff; ExpectedStatuses = @("failed") }
)

try {
    Write-Step "phase A: transient/success/fail patterns"
    $phaseAConfigPath = New-PhaseConfig -Phase "phase-a" -BaseConfigText $originalConfigText -FaultYaml (Get-PhaseAFaultYaml)
    $generatedConfigPaths.Add($phaseAConfigPath)
    Start-HomeControlStackForTest -Phase "phase-a" -ConfigPath $phaseAConfigPath
    foreach ($case in $phaseACases) {
        $result = Invoke-FaultCase -Case $case
        $results.Add($result)
        if (-not $result.status_ok) { $failed = $true }
        Start-Sleep -Seconds $DelayBetweenCasesSeconds
    }

    Write-Step "phase B: confirmation/timeout/duplicate/unsupported patterns"
    $phaseBConfigPath = New-PhaseConfig -Phase "phase-b" -BaseConfigText $originalConfigText -FaultYaml (Get-PhaseBFaultYaml)
    $generatedConfigPaths.Add($phaseBConfigPath)
    Start-HomeControlStackForTest -Phase "phase-b" -ConfigPath $phaseBConfigPath
    foreach ($case in $phaseBCases) {
        $result = Invoke-FaultCase -Case $case
        $results.Add($result)
        if (-not $result.status_ok) { $failed = $true }
        Start-Sleep -Seconds $DelayBetweenCasesSeconds
    }
}
catch {
    $runError = $_
}
finally {
    if (-not $KeepFaultConfig) {
        Write-Step "restore normal stack mode"
        if (-not $NoRestart -and -not $NoFinalRestart) {
            Start-HomeControlStackForTest -Phase "restore"
        }
        foreach ($path in $generatedConfigPaths) {
            Remove-GeneratedArtifact -Path $path -FileNamePattern "fault-e2e-$RunStamp-*-home-control.yaml"
        }
    }
    else {
        Write-Step "keeping generated fault config and current fault-mode stack by explicit request"
    }
    Remove-GeneratedArtifact -Path $script:NodeRunnerPath -FileNamePattern "fault-e2e-$RunStamp-runner.mjs"
}

$summaryPath = Join-Path $CacheDir "fault-e2e-$RunStamp-results.json"
($results | ConvertTo-Json -Depth 8) | Set-Content -LiteralPath $summaryPath -Encoding UTF8

Write-Step "summary: $summaryPath"
$results | Select-Object case, status_ok, statuses, expected_statuses, attempts, query | Format-Table -AutoSize

if ($null -ne $runError) {
    throw "Fault E2E aborted: $($runError.Exception.Message)"
}

if ($failed) {
    throw "One or more fault E2E cases failed. See $summaryPath"
}

Write-Step "all fault E2E cases passed"
