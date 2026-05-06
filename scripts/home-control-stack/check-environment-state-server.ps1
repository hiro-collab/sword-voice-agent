param(
    [string]$WorkspaceRoot = "",
    [int]$EnvironmentStatePort = 8790,
    [string]$BaseUrl = "",
    [string]$TokenEnvPath = "",
    [int]$TimeoutSeconds = 3,
    [switch]$Json
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

. (Join-Path $PSScriptRoot "resolve-home-control-workspace.ps1")
$WorkspaceRoot = Resolve-HomeControlWorkspaceRoot -WorkspaceRoot $WorkspaceRoot -ScriptRoot $PSScriptRoot

if ([string]::IsNullOrWhiteSpace($BaseUrl)) {
    $BaseUrl = "http://127.0.0.1:$EnvironmentStatePort"
}
$BaseUrl = $BaseUrl.TrimEnd("/")

if ([string]::IsNullOrWhiteSpace($TokenEnvPath)) {
    $TokenEnvPath = Join-Path $WorkspaceRoot "home-assistant-server\.env"
}

function Get-DotEnvValue {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    foreach ($line in Get-Content -LiteralPath $Path -Encoding UTF8) {
        if ($line -match "^\s*#") {
            continue
        }
        if ($line -match "^\s*$([regex]::Escape($Name))\s*=\s*(.*)\s*$") {
            $value = $matches[1].Trim()
            if (
                $value.Length -ge 2 -and
                (($value.StartsWith('"') -and $value.EndsWith('"')) -or
                 ($value.StartsWith("'") -and $value.EndsWith("'")))
            ) {
                return $value.Substring(1, $value.Length - 2)
            }
            return $value
        }
    }
    return ""
}

function Resolve-EnvironmentToken {
    foreach ($name in @("ENVIRONMENT_API_TOKEN", "HOME_CONTROL_API_TOKEN")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value.Trim()
        }
        $value = Get-DotEnvValue -Path $TokenEnvPath -Name $name
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value.Trim()
        }
    }
    throw "ENVIRONMENT_API_TOKEN or HOME_CONTROL_API_TOKEN was not found in process env or $TokenEnvPath"
}

function Invoke-JsonGet {
    param(
        [Parameter(Mandatory = $true)][string]$Url,
        [hashtable]$Headers = @{}
    )
    $response = Invoke-WebRequest -Uri $Url -UseBasicParsing -TimeoutSec $TimeoutSeconds -Headers $Headers
    return [pscustomobject]@{
        StatusCode = [int]$response.StatusCode
        Json = ($response.Content | ConvertFrom-Json)
    }
}

$token = Resolve-EnvironmentToken
$headers = @{ Authorization = "Bearer $token" }

$health = Invoke-JsonGet -Url "$BaseUrl/health"
$environment = Invoke-JsonGet -Url "$BaseUrl/environment/current" -Headers $headers
$indicators = Invoke-JsonGet -Url "$BaseUrl/indicators/current"

$summary = [ordered]@{
    base_url = $BaseUrl
    health_status = $health.StatusCode
    environment_status = $environment.StatusCode
    indicators_status = $indicators.StatusCode
    environment_stale = [bool]$environment.Json.stale
    observed_at = $environment.Json.observed_at
    appliance_count = @($environment.Json.appliances.PSObject.Properties).Count
    vision_count = @($environment.Json.vision.PSObject.Properties).Count
    source_names = @($environment.Json.sources.PSObject.Properties.Name)
    node_names = @($indicators.Json.nodes.PSObject.Properties.Name)
}

if ($Json) {
    $summary | ConvertTo-Json -Depth 8
    return
}

Write-Host "[environment-state] OK: $BaseUrl"
Write-Host "  /health              HTTP $($health.StatusCode)"
Write-Host "  /environment/current HTTP $($environment.StatusCode) stale=$($summary.environment_stale) observed_at=$($summary.observed_at)"
Write-Host "  /indicators/current  HTTP $($indicators.StatusCode)"
Write-Host "  appliances=$($summary.appliance_count) vision=$($summary.vision_count)"
Write-Host "  sources=$([string]::Join(', ', @($summary.source_names)))"
Write-Host "  nodes=$([string]::Join(', ', @($summary.node_names)))"
