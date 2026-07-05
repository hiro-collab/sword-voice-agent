param(
    [string]$WorkspaceRoot = "",
    [string]$AiTalkCoreRepoUrl = "https://github.com/hiro-collab/ai-talk-core.git",
    [string]$MediapipeSwordSignRepoUrl = "https://github.com/hiro-collab/mediapipe-sword-sign.git",
    [string]$TtsServiceRepoUrl = "https://github.com/hiro-collab/tts-service.git",
    [string]$AvatarServiceRepoUrl = "https://github.com/hiro-collab/avatar-service.git",
    [string]$EnvironmentStateServerRepoUrl = "https://github.com/hiro-collab/environment-state-server.git",
    [string]$HomeAssistantServerRepoUrl = "https://github.com/hiro-collab/home-assistant-server.git",
    [string]$AituberKitRepoUrl = "https://github.com/hiro-collab/aituber-kit-sword.git",
    [string]$TouchDesignerAiControllerRepoUrl = "https://github.com/hiro-collab/touchdesigner-ai-controller.git",
    [string]$SystemHouseRendererRepoUrl = "https://github.com/hiro-collab/system-house-renderer.git",
    [string]$EnvPath = ".env",
    [switch]$UpdateEnv,
    [switch]$NoPull,
    [switch]$DryRun
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

if ([string]::IsNullOrWhiteSpace($WorkspaceRoot)) {
    $WorkspaceRoot = Get-SwordWorkspaceRoot
}
else {
    $WorkspaceRoot = Resolve-SwordPath -Path $WorkspaceRoot
}

function Invoke-GitCommand {
    param(
        [Parameter(Mandatory = $true)][string[]]$Command,
        [string]$WorkingDirectory = $WorkspaceRoot
    )

    if ($DryRun) {
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

function Ensure-ValidationClone {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$RepoUrl
    )

    $target = Join-Path $WorkspaceRoot $Name
    $targetParent = Split-Path -Parent $target
    if (-not [string]::IsNullOrWhiteSpace($targetParent)) {
        New-Item -ItemType Directory -Force -Path $targetParent | Out-Null
    }
    if (-not (Test-Path -LiteralPath $target)) {
        Invoke-GitCommand -Command @("git", "clone", $RepoUrl, $target)
        return
    }

    $gitDir = Join-Path $target ".git"
    if (-not (Test-Path -LiteralPath $gitDir)) {
        throw "target exists but is not a git clone: $target"
    }

    if ($NoPull) {
        Write-Host "skip pull: $target"
        return
    }

    $status = & git -C $target status --short
    if (-not [string]::IsNullOrWhiteSpace(($status -join ""))) {
        Write-Host "skip pull because worktree is not clean: $target"
        return
    }

    Invoke-GitCommand -Command @("git", "-C", $target, "pull", "--ff-only")
}

function Set-EnvLine {
    param(
        [AllowEmptyString()][string[]]$Lines,
        [Parameter(Mandatory = $true)][string]$Name,
        [Parameter(Mandatory = $true)][string]$Value
    )

    $updated = $false
    $nextLines = @()
    foreach ($line in @($Lines)) {
        if ($line -match "^\s*$([regex]::Escape($Name))=") {
            $updated = $true
            $nextLines += "$Name=$Value"
        }
        else {
            $nextLines += $line
        }
    }
    if (-not $updated) {
        $nextLines += "$Name=$Value"
    }
    return ,$nextLines
}

function Update-ValidationEnv {
    $resolvedEnvPath = Resolve-SwordPath -Path $EnvPath
    if (-not (Test-Path -LiteralPath $resolvedEnvPath)) {
        $examplePath = Join-Path (Get-SwordRepoRoot) ".env.example"
        if (-not (Test-Path -LiteralPath $examplePath)) {
            throw ".env.example not found: $examplePath"
        }
        if ($DryRun) {
            Write-Host "Copy-Item $examplePath $resolvedEnvPath"
        }
        else {
            Copy-Item -LiteralPath $examplePath -Destination $resolvedEnvPath
        }
    }

    $lines = @(if (Test-Path -LiteralPath $resolvedEnvPath) {
        Get-Content -LiteralPath $resolvedEnvPath
    }
    else {
        Get-Content -LiteralPath (Join-Path (Get-SwordRepoRoot) ".env.example")
    })
    $lines = Set-EnvLine -Lines $lines -Name "AI_TALK_CORE_ROOT" -Value "..\organs\speech-input\ai-talk-core"
    $lines = Set-EnvLine -Lines $lines -Name "MEDIAPIPE_SWORD_SIGN_ROOT" -Value "..\organs\reflex\mediapipe-sword-sign"
    $lines = Set-EnvLine -Lines $lines -Name "TTS_SERVICE_ROOT" -Value "..\organs\expression\tts-service"
    $lines = Set-EnvLine -Lines $lines -Name "AVATAR_SERVICE_ROOT" -Value "..\organs\expression\avatar-service"
    $lines = Set-EnvLine -Lines $lines -Name "SYSTEM_HOUSE_RENDERER_ROOT" -Value "..\organs\diagnostics\system-house-renderer"
    $lines = Set-EnvLine -Lines $lines -Name "AI_TALK_CORE_RUNTIME_STATUS_FILE" -Value ".cache\sword_voice_agent\runtime\ai_talk_core.json"
    $lines = Set-EnvLine -Lines $lines -Name "MEDIAPIPE_SWORD_SIGN_RUNTIME_STATUS_FILE" -Value ".cache\sword_voice_agent\runtime\mediapipe_udp_publisher.json"
    $lines = Set-EnvLine -Lines $lines -Name "MEDIAPIPE_SWORD_SIGN_CONTROL_HTTP_HOST" -Value "127.0.0.1"
    $lines = Set-EnvLine -Lines $lines -Name "MEDIAPIPE_SWORD_SIGN_CONTROL_HTTP_PORT" -Value "18765"
    $lines = Set-EnvLine -Lines $lines -Name "MEDIAPIPE_SWORD_SIGN_CONTROL_TOKEN" -Value ""
    $lines = Set-EnvLine -Lines $lines -Name "TTS_VOLUME_URL" -Value "http://127.0.0.1:8765/api/volume"
    $lines = Set-EnvLine -Lines $lines -Name "TTS_VOLUME_PREVIEW_URL" -Value "http://127.0.0.1:8765/api/volume/preview"
    $lines = Set-EnvLine -Lines $lines -Name "TTS_SERVICE_RUNTIME_STATUS_FILE" -Value ".cache\sword_voice_agent\runtime\tts_service.json"
    $lines = Set-EnvLine -Lines $lines -Name "TTS_SERVICE_SHUTDOWN_TOKEN" -Value ""
    $lines = Set-EnvLine -Lines $lines -Name "AVATAR_MODEL_URL" -Value ""
    $lines = Set-EnvLine -Lines $lines -Name "AVATAR_SERVICE_RUNTIME_STATUS_FILE" -Value ".cache\sword_voice_agent\runtime\avatar_service.json"
    $lines = Set-EnvLine -Lines $lines -Name "SYSTEM_HOUSE_RENDERER_RUNTIME_STATUS_FILE" -Value ".cache\sword_voice_agent\runtime\system_house_renderer.json"

    if ($DryRun) {
        Write-Host "Set validation module roots in $resolvedEnvPath"
        return
    }
    Set-Content -LiteralPath $resolvedEnvPath -Value $lines -Encoding UTF8
}

New-Item -ItemType Directory -Force -Path $WorkspaceRoot | Out-Null
Ensure-ValidationClone -Name "organs\speech-input\ai-talk-core" -RepoUrl $AiTalkCoreRepoUrl
Ensure-ValidationClone -Name "organs\reflex\mediapipe-sword-sign" -RepoUrl $MediapipeSwordSignRepoUrl
Ensure-ValidationClone -Name "organs\expression\tts-service" -RepoUrl $TtsServiceRepoUrl
Ensure-ValidationClone -Name "organs\expression\avatar-service" -RepoUrl $AvatarServiceRepoUrl
Ensure-ValidationClone -Name "organs\environment\environment-state-server" -RepoUrl $EnvironmentStateServerRepoUrl
Ensure-ValidationClone -Name "organs\action\home-assistant-server" -RepoUrl $HomeAssistantServerRepoUrl
Ensure-ValidationClone -Name "organs\expression\aituber-kit" -RepoUrl $AituberKitRepoUrl
Ensure-ValidationClone -Name "organs\display\touchdesigner-ai-controller" -RepoUrl $TouchDesignerAiControllerRepoUrl
Ensure-ValidationClone -Name "organs\diagnostics\system-house-renderer" -RepoUrl $SystemHouseRendererRepoUrl

if ($UpdateEnv) {
    Update-ValidationEnv
}

Write-Host "validation module roots:"
Write-Host "AI_TALK_CORE_ROOT=..\organs\speech-input\ai-talk-core"
Write-Host "MEDIAPIPE_SWORD_SIGN_ROOT=..\organs\reflex\mediapipe-sword-sign"
Write-Host "TTS_SERVICE_ROOT=..\organs\expression\tts-service"
Write-Host "AVATAR_SERVICE_ROOT=..\organs\expression\avatar-service"
Write-Host "SYSTEM_HOUSE_RENDERER_ROOT=..\organs\diagnostics\system-house-renderer"
Write-Host "additional sibling clones:"
Write-Host "organs\environment\environment-state-server"
Write-Host "organs\action\home-assistant-server"
Write-Host "organs\expression\aituber-kit"
Write-Host "organs\display\touchdesigner-ai-controller"
