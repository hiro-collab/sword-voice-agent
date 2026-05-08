param(
    [string]$WorkspaceRoot = "",
    [string]$SwordEnvPath = "",
    [string]$HomeAssistantEnvPath = "",
    [string]$DifyBaseUrl = "",
    [string]$EnvironmentUrl = "",
    [string]$WorkflowPath = "",
    [int]$TimeoutSeconds = 10,
    [int]$MinRoomLightLearningLevel = -1,
    [int]$RoomLightFeedbackCountGreaterThan = -1,
    [switch]$FailOnLearningError,
    [switch]$Json
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$utf8NoBom = [System.Text.UTF8Encoding]::new($false)
[Console]::OutputEncoding = $utf8NoBom
$OutputEncoding = $utf8NoBom

. (Join-Path $PSScriptRoot "resolve-home-control-workspace.ps1")
$WorkspaceRoot = Resolve-HomeControlWorkspaceRoot -WorkspaceRoot $WorkspaceRoot -ScriptRoot $PSScriptRoot
$RepoRoot = Join-Path $WorkspaceRoot "sword-control-plane"

if ([string]::IsNullOrWhiteSpace($SwordEnvPath)) {
    $SwordEnvPath = Join-Path $RepoRoot ".env"
}
if ([string]::IsNullOrWhiteSpace($HomeAssistantEnvPath)) {
    $HomeAssistantEnvPath = Join-Path $WorkspaceRoot "organs\action\home-assistant-server\.env"
}
if ([string]::IsNullOrWhiteSpace($WorkflowPath)) {
    $WorkflowPath = Join-Path $RepoRoot "dify-apps\Home Control Assistant.issue-iteration.yml"
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
        if ($line -match "^\s*#" -or $line -match "^\s*$") {
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

function Get-ConfigValue {
    param(
        [Parameter(Mandatory = $true)][string]$Name,
        [string[]]$Paths = @()
    )
    $value = [Environment]::GetEnvironmentVariable($Name, "Process")
    if (-not [string]::IsNullOrWhiteSpace($value)) {
        return $value.Trim()
    }
    foreach ($path in $Paths) {
        $value = Get-DotEnvValue -Path $path -Name $Name
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            return $value.Trim()
        }
    }
    return ""
}

function Get-WorkflowVersion {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    $text = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if ($text -match 'HCA_WORKFLOW_VERSION\s*=\s*"([^"]+)"') {
        return $matches[1]
    }
    return ""
}

function Get-FileSha256 {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not (Test-Path -LiteralPath $Path -PathType Leaf)) {
        return ""
    }
    return (Get-FileHash -LiteralPath $Path -Algorithm SHA256).Hash.ToLowerInvariant()
}

function Invoke-JsonRequest {
    param(
        [Parameter(Mandatory = $true)][string]$Method,
        [Parameter(Mandatory = $true)][string]$Url,
        [hashtable]$Headers = @{},
        [object]$Body = $null
    )
    try {
        $params = @{
            Method = $Method
            Uri = $Url
            UseBasicParsing = $true
            TimeoutSec = $TimeoutSeconds
            Headers = $Headers
        }
        if ($null -ne $Body) {
            $params["ContentType"] = "application/json; charset=utf-8"
            $params["Body"] = ($Body | ConvertTo-Json -Depth 12 -Compress)
        }
        $response = Invoke-WebRequest @params
        $json = $null
        if (-not [string]::IsNullOrWhiteSpace($response.Content)) {
            $json = $response.Content | ConvertFrom-Json
        }
        return [pscustomobject]@{
            ok = $true
            status_code = [int]$response.StatusCode
            json = $json
            content = [string]$response.Content
            error = ""
        }
    }
    catch {
        $statusCode = 0
        $response = $null
        $responseProperty = $_.Exception.PSObject.Properties["Response"]
        if ($null -ne $responseProperty) {
            $response = $responseProperty.Value
        }
        if ($null -ne $response -and $null -ne $response.StatusCode) {
            $statusCode = [int]$response.StatusCode
        }
        return [pscustomobject]@{
            ok = $false
            status_code = $statusCode
            json = $null
            content = ""
            error = $_.Exception.Message
        }
    }
}

function Get-JsonObjectAfterMarker {
    param(
        [Parameter(Mandatory = $true)][string]$Text,
        [string]$Marker = "HCA_DIAGNOSTIC_JSON"
    )
    $markerIndex = $Text.IndexOf($Marker, [StringComparison]::Ordinal)
    if ($markerIndex -lt 0) {
        return $null
    }
    $start = $Text.IndexOf("{", $markerIndex)
    if ($start -lt 0) {
        return $null
    }

    $depth = 0
    $inString = $false
    $escaped = $false
    for ($i = $start; $i -lt $Text.Length; $i++) {
        $ch = $Text[$i]
        if ($inString) {
            if ($escaped) {
                $escaped = $false
            }
            elseif ($ch -eq "\") {
                $escaped = $true
            }
            elseif ($ch -eq '"') {
                $inString = $false
            }
            continue
        }

        if ($ch -eq '"') {
            $inString = $true
        }
        elseif ($ch -eq "{") {
            $depth++
        }
        elseif ($ch -eq "}") {
            $depth--
            if ($depth -eq 0) {
                return $Text.Substring($start, $i - $start + 1)
            }
        }
    }
    return $null
}

function Get-PropertyValue {
    param(
        [object]$Object,
        [Parameter(Mandatory = $true)][string]$Name
    )
    if ($null -eq $Object) {
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Get-RoomLightQuery {
    param([object]$Environment)
    $stateQueries = Get-PropertyValue -Object $Environment -Name "state_queries"
    if ($null -eq $stateQueries) {
        return $null
    }
    return Get-PropertyValue -Object $stateQueries -Name "room_light"
}

function Get-LightAppliance {
    param([object]$Environment)
    $appliances = Get-PropertyValue -Object $Environment -Name "appliances"
    if ($null -eq $appliances) {
        return $null
    }
    return Get-PropertyValue -Object $appliances -Name "light"
}

function Get-EnvironmentFeedbackSummaryUrl {
    param(
        [Parameter(Mandatory = $true)][string]$EnvironmentUrl,
        [Parameter(Mandatory = $true)][string]$SwordEnvPath
    )
    $feedbackUrl = Get-ConfigValue -Name "ENVIRONMENT_FEEDBACK_URL" -Paths @($SwordEnvPath)
    if ([string]::IsNullOrWhiteSpace($feedbackUrl)) {
        $feedbackUrl = $EnvironmentUrl
    }
    $feedbackUrl = $feedbackUrl.Replace("host.docker.internal", "127.0.0.1").TrimEnd("/")
    if ($feedbackUrl -match "/environment/current$") {
        $feedbackUrl = $feedbackUrl -replace "/environment/current$", "/feedback/state-query"
    }
    if ($feedbackUrl -notmatch "/feedback/state-query$") {
        $feedbackUrl = "$feedbackUrl/feedback/state-query"
    }
    return "$feedbackUrl/summary?target=room_light"
}

if ([string]::IsNullOrWhiteSpace($DifyBaseUrl)) {
    $DifyBaseUrl = Get-ConfigValue -Name "DIFY_BASE_URL" -Paths @($SwordEnvPath)
    if ([string]::IsNullOrWhiteSpace($DifyBaseUrl)) {
        $DifyBaseUrl = "http://localhost:8080/v1"
    }
}
$DifyBaseUrl = $DifyBaseUrl.TrimEnd("/")

if ([string]::IsNullOrWhiteSpace($EnvironmentUrl)) {
    $EnvironmentUrl = Get-ConfigValue -Name "ENVIRONMENT_STATE_URL" -Paths @($SwordEnvPath)
    if ([string]::IsNullOrWhiteSpace($EnvironmentUrl)) {
        $EnvironmentUrl = "http://127.0.0.1:8790/environment/current"
    }
    $EnvironmentUrl = $EnvironmentUrl.Replace("host.docker.internal", "127.0.0.1")
}
$EnvironmentFeedbackSummaryUrl = Get-EnvironmentFeedbackSummaryUrl -EnvironmentUrl $EnvironmentUrl -SwordEnvPath $SwordEnvPath

$difyApiKey = Get-ConfigValue -Name "DIFY_API_KEY" -Paths @($SwordEnvPath)
$environmentToken = Get-ConfigValue -Name "ENVIRONMENT_API_TOKEN" -Paths @($SwordEnvPath, $HomeAssistantEnvPath)
if ([string]::IsNullOrWhiteSpace($environmentToken)) {
    $environmentToken = Get-ConfigValue -Name "HOME_CONTROL_API_TOKEN" -Paths @($SwordEnvPath, $HomeAssistantEnvPath)
}

$localWorkflowVersion = Get-WorkflowVersion -Path $WorkflowPath
$localYamlSha256 = Get-FileSha256 -Path $WorkflowPath

$difyHeaders = @{
    Authorization = "Bearer $difyApiKey"
    Accept = "application/json"
}
$environmentHeaders = @{
    Authorization = "Bearer $environmentToken"
    Accept = "application/json"
}

$parameters = if ([string]::IsNullOrWhiteSpace($difyApiKey)) {
    [pscustomobject]@{ ok = $false; status_code = 0; json = $null; content = ""; error = "DIFY_API_KEY is missing" }
}
else {
    Invoke-JsonRequest -Method "GET" -Url "$DifyBaseUrl/parameters" -Headers $difyHeaders
}

$environment = if ([string]::IsNullOrWhiteSpace($environmentToken)) {
    [pscustomobject]@{ ok = $false; status_code = 0; json = $null; content = ""; error = "ENVIRONMENT_API_TOKEN or HOME_CONTROL_API_TOKEN is missing" }
}
else {
    Invoke-JsonRequest -Method "GET" -Url $EnvironmentUrl -Headers $environmentHeaders
}

$feedbackSummaryResponse = if ([string]::IsNullOrWhiteSpace($environmentToken)) {
    [pscustomobject]@{ ok = $false; status_code = 0; json = $null; content = ""; error = "ENVIRONMENT_API_TOKEN or HOME_CONTROL_API_TOKEN is missing" }
}
else {
    Invoke-JsonRequest -Method "GET" -Url $EnvironmentFeedbackSummaryUrl -Headers $environmentHeaders
}

$diagnosticPayload = $null
$diagnosticAnswer = ""
$difyDiagnostic = if ([string]::IsNullOrWhiteSpace($difyApiKey)) {
    [pscustomobject]@{ ok = $false; status_code = 0; json = $null; content = ""; error = "DIFY_API_KEY is missing" }
}
else {
    $body = @{
        inputs = @{}
        query = "__HCA_DIAGNOSTIC__ latest-yaml-check"
        response_mode = "blocking"
        conversation_id = ""
        user = "codex-home-control-diagnostic"
    }
    Invoke-JsonRequest -Method "POST" -Url "$DifyBaseUrl/chat-messages" -Headers $difyHeaders -Body $body
}
if ($difyDiagnostic.ok -and $null -ne $difyDiagnostic.json) {
    $diagnosticAnswer = [string](Get-PropertyValue -Object $difyDiagnostic.json -Name "answer")
    $jsonText = Get-JsonObjectAfterMarker -Text $diagnosticAnswer
    if (-not [string]::IsNullOrWhiteSpace($jsonText)) {
        try {
            $diagnosticPayload = $jsonText | ConvertFrom-Json
        }
        catch {
            $diagnosticPayload = $null
        }
    }
}

$directRoomLight = Get-RoomLightQuery -Environment $environment.json
$directLightAppliance = Get-LightAppliance -Environment $environment.json
$reportedVersion = [string](Get-PropertyValue -Object $diagnosticPayload -Name "workflow_version")
$diagnosticFeedbackContract = Get-PropertyValue -Object $diagnosticPayload -Name "feedback_contract"
$diagnosticPostActionLightFeedback = [bool](Get-PropertyValue -Object $diagnosticFeedbackContract -Name "post_action_light_feedback")
$diagnosticWaitForRoomLight = Get-PropertyValue -Object $diagnosticFeedbackContract -Name "wait_for_room_light"
$diagnosticEnvironment = Get-PropertyValue -Object $diagnosticPayload -Name "environment"
$diagnosticRoomLight = Get-PropertyValue -Object $diagnosticEnvironment -Name "room_light"
$diagnosticLightAppliance = Get-PropertyValue -Object $diagnosticEnvironment -Name "light_appliance"
$diagnosticStateQueryPresent = [bool](Get-PropertyValue -Object $diagnosticEnvironment -Name "state_query_present")
$diagnosticEnvironmentStatus = Get-PropertyValue -Object $diagnosticEnvironment -Name "environment_status_code"
$feedbackSummary = Get-PropertyValue -Object $feedbackSummaryResponse.json -Name "summary"
$roomLightLearning = Get-PropertyValue -Object $feedbackSummary -Name "learning"
$roomLightLearningLevelIndex = -1
if ($null -ne $roomLightLearning) {
    $rawLearningLevel = Get-PropertyValue -Object $roomLightLearning -Name "level_index"
    if ($null -ne $rawLearningLevel) {
        $roomLightLearningLevelIndex = [int]$rawLearningLevel
    }
}
$roomLightLearningAcceptedCount = -1
if ($null -ne $roomLightLearning) {
    $rawLearningCount = Get-PropertyValue -Object $roomLightLearning -Name "accepted_count"
    if ($null -ne $rawLearningCount) {
        $roomLightLearningAcceptedCount = [int]$rawLearningCount
    }
}

$versionMatch = (
    -not [string]::IsNullOrWhiteSpace($localWorkflowVersion) -and
    -not [string]::IsNullOrWhiteSpace($reportedVersion) -and
    $localWorkflowVersion -eq $reportedVersion
)

$likelyIssue = "ok"
$advice = "Dify workflow version and Environment state query look visible."
if (-not $parameters.ok) {
    $likelyIssue = "dify_api_key_or_base_url"
    $advice = "Dify /parameters failed. Check DIFY_BASE_URL and DIFY_API_KEY."
}
elseif (-not $difyDiagnostic.ok) {
    $likelyIssue = "dify_chat_messages_failed"
    $advice = "Dify /chat-messages failed. Check app API key, app publication, and Dify logs."
}
elseif ($null -eq $diagnosticPayload) {
    $likelyIssue = "diagnostic_marker_missing"
    $advice = "The published Dify app did not return HCA_DIAGNOSTIC_JSON. Import and publish the latest YAML, then retry."
}
elseif (-not $versionMatch) {
    $likelyIssue = "workflow_version_mismatch"
    $advice = "The published Dify app version differs from the local YAML. Re-import and publish the latest workflow."
}
elseif (-not $diagnosticPostActionLightFeedback -or $null -eq $diagnosticWaitForRoomLight) {
    $likelyIssue = "workflow_contract_missing"
    $advice = "The published Dify app does not report the post-action feedback / wait_for contract. Re-import and publish the latest workflow."
}
elseif (-not $environment.ok) {
    $likelyIssue = "local_environment_unreachable"
    $advice = "Local Environment State Server check failed. Start the stack or check the token."
}
elseif (-not $feedbackSummaryResponse.ok) {
    $likelyIssue = "room_light_feedback_summary_unreachable"
    $advice = "Environment feedback summary check failed. Check Environment State Server and token."
}
elseif ($null -eq $directRoomLight) {
    $likelyIssue = "environment_room_light_missing"
    $advice = "Environment is reachable, but state_queries.room_light is missing locally. Check camera/snapshot ingestion."
}
elseif (-not $diagnosticStateQueryPresent) {
    $likelyIssue = "dify_environment_state_not_visible"
    $advice = "Local Environment has room_light, but Dify diagnostic does not. Check Dify app ENVIRONMENT_STATE_URL and HOME_CONTROL_API_TOKEN."
}
elseif ($MinRoomLightLearningLevel -ge 0 -and $roomLightLearningLevelIndex -lt $MinRoomLightLearningLevel) {
    $likelyIssue = "room_light_learning_level_too_low"
    $advice = "room_light learning level is $roomLightLearningLevelIndex, expected at least $MinRoomLightLearningLevel."
}
elseif ($RoomLightFeedbackCountGreaterThan -ge 0 -and $roomLightLearningAcceptedCount -le $RoomLightFeedbackCountGreaterThan) {
    $likelyIssue = "room_light_feedback_count_not_increased"
    $advice = "room_light feedback count is $roomLightLearningAcceptedCount, expected greater than $RoomLightFeedbackCountGreaterThan."
}
elseif ($FailOnLearningError -and $null -ne $roomLightLearning -and -not [bool](Get-PropertyValue -Object $roomLightLearning -Name "ok")) {
    $likelyIssue = "room_light_learning_error"
    $advice = "room_light learning reports an error. Check feedback payloads and learning.problems."
}

$summary = [ordered]@{
    ok = ($likelyIssue -eq "ok")
    likely_issue = $likelyIssue
    advice = $advice
    local = [ordered]@{
        workflow_path = $WorkflowPath
        workflow_version = $localWorkflowVersion
        yaml_sha256 = $localYamlSha256
    }
    dify = [ordered]@{
        base_url = $DifyBaseUrl
        parameters_ok = [bool]$parameters.ok
        parameters_status = [int]$parameters.status_code
        diagnostic_ok = [bool]$difyDiagnostic.ok
        diagnostic_status = [int]$difyDiagnostic.status_code
        reported_workflow_version = $reportedVersion
        workflow_version_match = [bool]$versionMatch
        post_action_light_feedback = [bool]$diagnosticPostActionLightFeedback
        wait_for_room_light = $diagnosticWaitForRoomLight
        feedback_contract = $diagnosticFeedbackContract
        answer_preview = if ($diagnosticAnswer.Length -gt 500) { $diagnosticAnswer.Substring(0, 500) } else { $diagnosticAnswer }
    }
    environment_direct = [ordered]@{
        url = $EnvironmentUrl
        ok = [bool]$environment.ok
        status = [int]$environment.status_code
        snapshot_id = if ($environment.json) { [string](Get-PropertyValue -Object $environment.json -Name "snapshot_id") } else { "" }
        light_appliance = $directLightAppliance
        state_query_present = ($null -ne $directRoomLight)
        room_light = $directRoomLight
        feedback_summary_url = $EnvironmentFeedbackSummaryUrl
        feedback_summary_status = [int]$feedbackSummaryResponse.status_code
        room_light_feedback_summary = $feedbackSummary
        room_light_learning = $roomLightLearning
    }
    environment_seen_by_dify = [ordered]@{
        status = $diagnosticEnvironmentStatus
        state_query_present = $diagnosticStateQueryPresent
        state_query_keys = Get-PropertyValue -Object $diagnosticEnvironment -Name "state_query_keys"
        light_appliance = $diagnosticLightAppliance
        room_light = $diagnosticRoomLight
        detail = Get-PropertyValue -Object $diagnosticEnvironment -Name "environment_detail"
    }
}

if ($Json) {
    $summary | ConvertTo-Json -Depth 14
    return
}

Write-Host "[hca-dify-check] $($summary.likely_issue)"
Write-Host "  advice: $($summary.advice)"
Write-Host "  local workflow: $($summary.local.workflow_version)"
Write-Host "  Dify reported:  $($summary.dify.reported_workflow_version)"
Write-Host "  version match:  $($summary.dify.workflow_version_match)"
Write-Host "  Dify API:       parameters=$($summary.dify.parameters_status) chat=$($summary.dify.diagnostic_status)"
Write-Host "  Dify contract:  post_action_light_feedback=$($summary.dify.post_action_light_feedback) wait_for_room_light=$($null -ne $summary.dify.wait_for_room_light)"
Write-Host "  Environment:    direct_ok=$($summary.environment_direct.ok) status=$($summary.environment_direct.status) room_light=$($summary.environment_direct.state_query_present)"
if ($summary.environment_direct.light_appliance) {
    Write-Host "  direct light:    state=$($summary.environment_direct.light_appliance.state) stale=$($summary.environment_direct.light_appliance.stale) source=$($summary.environment_direct.light_appliance.source) updated_at=$($summary.environment_direct.light_appliance.updated_at)"
}
$learningLevel = ""
$learningLevelIndex = ""
$learningAcceptedCount = ""
$learningOk = ""
if ($null -ne $summary.environment_direct.room_light_learning) {
    $learningLevel = [string](Get-PropertyValue -Object $summary.environment_direct.room_light_learning -Name "level")
    $learningLevelIndex = [string](Get-PropertyValue -Object $summary.environment_direct.room_light_learning -Name "level_index")
    $learningAcceptedCount = [string](Get-PropertyValue -Object $summary.environment_direct.room_light_learning -Name "accepted_count")
    $learningOk = [string](Get-PropertyValue -Object $summary.environment_direct.room_light_learning -Name "ok")
}
Write-Host "  Learning:       status=$($summary.environment_direct.feedback_summary_status) level=$learningLevel($learningLevelIndex) accepted=$learningAcceptedCount ok=$learningOk"
Write-Host "  Dify sees env:  status=$($summary.environment_seen_by_dify.status) room_light=$($summary.environment_seen_by_dify.state_query_present)"
if ($summary.environment_seen_by_dify.light_appliance) {
    Write-Host "  Dify light:      state=$($summary.environment_seen_by_dify.light_appliance.state) stale=$($summary.environment_seen_by_dify.light_appliance.stale) source=$($summary.environment_seen_by_dify.light_appliance.source) updated_at=$($summary.environment_seen_by_dify.light_appliance.updated_at)"
}
if ($summary.environment_direct.room_light) {
    Write-Host "  direct room_light: state=$($summary.environment_direct.room_light.state) confidence=$($summary.environment_direct.room_light.confidence_label) stale=$($summary.environment_direct.room_light.stale) authority=$($summary.environment_direct.room_light.authority)"
}
if ($summary.environment_seen_by_dify.room_light) {
    Write-Host "  Dify room_light:   state=$($summary.environment_seen_by_dify.room_light.state) confidence=$($summary.environment_seen_by_dify.room_light.confidence_label) stale=$($summary.environment_seen_by_dify.room_light.stale) authority=$($summary.environment_seen_by_dify.room_light.authority)"
}
