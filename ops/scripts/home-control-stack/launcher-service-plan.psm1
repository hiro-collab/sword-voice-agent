Set-StrictMode -Version Latest
$ErrorActionPreference = "Stop"

$script:MaximumPlanBytes = 262144
$script:Sha256Pattern = "^[a-f0-9]{64}$"
$script:EnvironmentNamePattern = "^[A-Z][A-Z0-9_]{0,127}$"
$script:ReservedEnvironmentNames = @("SWORD_LAUNCHER_N1_PRIVATE_PLAN_FILE", "SWORD_LAUNCHER_N1_PRIVATE_LEASE_PROOF")
$script:Descriptors = [ordered]@{
    home_assistant_bridge = [pscustomobject]@{
        ServiceId = "home_assistant_bridge"; Requirement = "required"; Ownership = "owned"
        ExecutableNames = @("uv"); DefaultListenerPort = 8787
    }
    environment_state_server = [pscustomobject]@{
        ServiceId = "environment_state_server"; Requirement = "required"; Ownership = "owned"
        ExecutableNames = @("uv"); DefaultListenerPort = 8790
    }
    openai_provider_broker = [pscustomobject]@{
        ServiceId = "openai_provider_broker"; Requirement = "required"; Ownership = "owned"
        ExecutableNames = @("uv"); DefaultListenerPort = 18786
    }
    thought_core_api = [pscustomobject]@{
        ServiceId = "thought_core_api"; Requirement = "required"; Ownership = "owned"
        ExecutableNames = @("pwsh", "powershell"); DefaultListenerPort = 18787
    }
    mediapipe_camera_hub_stack = [pscustomobject]@{
        ServiceId = "mediapipe_camera_hub_stack"; Requirement = "optional"; Ownership = "owned"
        ExecutableNames = @("uv"); DefaultListenerPort = 8765
    }
    vision_snapshot_processor = [pscustomobject]@{
        ServiceId = "vision_snapshot_processor"; Requirement = "optional"; Ownership = "owned"
        ExecutableNames = @("uv"); DefaultListenerPort = 8776
    }
    aituber_kit = [pscustomobject]@{
        ServiceId = "aituber_kit"; Requirement = "required"; Ownership = "owned"
        ExecutableNames = @("node"); DefaultListenerPort = 3000
    }
    thought_core_watcher = [pscustomobject]@{
        ServiceId = "thought_core_watcher"; Requirement = "required"; Ownership = "owned"
        ExecutableNames = @("pwsh", "powershell"); DefaultListenerPort = 0
    }
    touchdesigner_control_gui = [pscustomobject]@{
        ServiceId = "touchdesigner_control_gui"; Requirement = "required"; Ownership = "owned"
        ExecutableNames = @("node"); DefaultListenerPort = 8788
    }
    voicevox = [pscustomobject]@{
        ServiceId = "voicevox"; Requirement = "external"; Ownership = "external"
        ExecutableNames = @(); DefaultListenerPort = 50021
    }
}

function Get-LauncherProperty {
    param(
        [Parameter(Mandatory = $true)][object]$InputObject,
        [Parameter(Mandatory = $true)][string]$Name
    )
    $property = $InputObject.PSObject.Properties[$Name]
    if ($null -eq $property) { throw "launcher_private_plan_invalid" }
    return $property.Value
}

function Assert-LauncherExactKeys {
    param(
        [Parameter(Mandatory = $true)][object]$InputObject,
        [Parameter(Mandatory = $true)][string[]]$Expected
    )
    $actual = @($InputObject.PSObject.Properties.Name | Sort-Object)
    $wanted = @($Expected | Sort-Object)
    if (($actual -join "`n") -cne ($wanted -join "`n")) { throw "launcher_private_plan_invalid" }
}

function Assert-LauncherBoundedString {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [int]$Maximum = 4096,
        [switch]$AllowEmpty
    )
    if ($Value -isnot [string] -or $Value.Length -gt $Maximum -or (-not $AllowEmpty -and $Value.Length -eq 0) -or $Value.Contains([char]0)) {
        throw "launcher_private_plan_invalid"
    }
    return [string]$Value
}

function Get-LauncherServiceIds {
    return @($script:Descriptors.Keys)
}

function Get-LauncherServiceDescriptor {
    param([Parameter(Mandatory = $true)][string]$ServiceId)
    if (-not $script:Descriptors.Contains($ServiceId)) { throw "launcher_service_plan_unknown" }
    $source = $script:Descriptors[$ServiceId]
    return [pscustomobject]@{
        ServiceId = [string]$source.ServiceId
        Requirement = [string]$source.Requirement
        Ownership = [string]$source.Ownership
        ExecutableNames = @($source.ExecutableNames)
        DefaultListenerPort = [int]$source.DefaultListenerPort
    }
}

function ConvertTo-LauncherEnvironmentMap {
    param([Parameter(Mandatory = $true)][object]$Value)
    $result = @{}
    $properties = @($Value.PSObject.Properties)
    if ($properties.Count -gt 64) { throw "launcher_private_plan_invalid" }
    foreach ($property in $properties) {
        if ([string]$property.Name -cnotmatch $script:EnvironmentNamePattern) { throw "launcher_private_plan_invalid" }
        if ($script:ReservedEnvironmentNames -ccontains [string]$property.Name) { throw "launcher_private_plan_invalid" }
        $result[[string]$property.Name] = Assert-LauncherBoundedString -Value $property.Value -Maximum 8192 -AllowEmpty
    }
    return $result
}

function ConvertTo-LauncherStringArray {
    param(
        [Parameter(Mandatory = $true)][AllowEmptyCollection()][object[]]$Value,
        [int]$MaximumItems = 128,
        [int]$MaximumLength = 4096,
        [string]$Pattern = ""
    )
    if ($Value -isnot [System.Collections.IEnumerable] -or $Value -is [string]) { throw "launcher_private_plan_invalid" }
    $items = @($Value)
    if ($items.Count -gt $MaximumItems) { throw "launcher_private_plan_invalid" }
    $result = @()
    foreach ($item in $items) {
        $text = Assert-LauncherBoundedString -Value $item -Maximum $MaximumLength -AllowEmpty
        if (-not [string]::IsNullOrEmpty($Pattern) -and $text -cnotmatch $Pattern) { throw "launcher_private_plan_invalid" }
        $result += $text
    }
    return $result
}

function Assert-LauncherAituberPlan {
    param(
        [Parameter(Mandatory = $true)][string]$FilePath,
        [Parameter(Mandatory = $true)][string[]]$Arguments,
        [Parameter(Mandatory = $true)][string]$WorkingDirectory,
        [Parameter(Mandatory = $true)][int]$ListenerPort
    )
    try {
        $nodeItem = Get-Item -LiteralPath $FilePath -Force -ErrorAction Stop
        $workingItem = Get-Item -LiteralPath $WorkingDirectory -Force -ErrorAction Stop
        $nodeCommands = @(Get-Command node -CommandType Application -ErrorAction Stop)
        $expectedEntrypoint = [IO.Path]::GetFullPath((Join-Path $WorkingDirectory "node_modules\next\dist\bin\next"))
        $entrypointItem = Get-Item -LiteralPath $expectedEntrypoint -Force -ErrorAction Stop
    }
    catch {
        throw "launcher_private_plan_invalid"
    }
    if (
        $nodeCommands.Count -lt 1 -or
        $nodeItem.PSIsContainer -or
        ($nodeItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        -not $workingItem.PSIsContainer -or
        ($workingItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        $entrypointItem.PSIsContainer -or
        ($entrypointItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0 -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals($nodeItem.FullName, [string]$nodeCommands[0].Source) -or
        $Arguments.Count -ne 6 -or
        -not [StringComparer]::OrdinalIgnoreCase.Equals([IO.Path]::GetFullPath($Arguments[0]), $entrypointItem.FullName) -or
        $Arguments[1] -cne "dev" -or
        $Arguments[2] -cne "--hostname" -or
        @("127.0.0.1", "localhost") -cnotcontains $Arguments[3].ToLowerInvariant() -or
        $Arguments[4] -cne "--port" -or
        $Arguments[5] -cne [string]$ListenerPort
    ) {
        throw "launcher_private_plan_invalid"
    }
}

function ConvertTo-LauncherOwnedPlan {
    param(
        [Parameter(Mandatory = $true)][object]$Value,
        [Parameter(Mandatory = $true)][object]$Descriptor
    )
    Assert-LauncherExactKeys -InputObject $Value -Expected @(
        "service_id", "file_path", "arguments", "working_directory", "environment",
        "remove_environment", "clear_inherited_environment", "listener_port"
    )
    $serviceId = Assert-LauncherBoundedString -Value (Get-LauncherProperty $Value "service_id") -Maximum 64
    if ($serviceId -cne $Descriptor.ServiceId) { throw "launcher_private_plan_invalid" }
    $filePath = Assert-LauncherBoundedString -Value (Get-LauncherProperty $Value "file_path") -Maximum 1024
    $workingDirectory = Assert-LauncherBoundedString -Value (Get-LauncherProperty $Value "working_directory") -Maximum 1024
    if (-not [IO.Path]::IsPathFullyQualified($filePath) -or -not [IO.Path]::IsPathFullyQualified($workingDirectory)) {
        throw "launcher_private_plan_invalid"
    }
    $executableName = [IO.Path]::GetFileName($filePath).ToLowerInvariant()
    $executableStem = [IO.Path]::GetFileNameWithoutExtension($filePath).ToLowerInvariant()
    if (($Descriptor.ExecutableNames -notcontains $executableName) -and ($Descriptor.ExecutableNames -notcontains $executableStem)) {
        throw "launcher_private_plan_invalid"
    }
    $argumentsValue = $Value.PSObject.Properties["arguments"].Value
    $removeEnvironmentValue = $Value.PSObject.Properties["remove_environment"].Value
    $arguments = ConvertTo-LauncherStringArray -Value $argumentsValue
    $removeEnvironment = ConvertTo-LauncherStringArray `
        -Value $removeEnvironmentValue `
        -MaximumItems 64 `
        -MaximumLength 128 `
        -Pattern $script:EnvironmentNamePattern
    $environment = ConvertTo-LauncherEnvironmentMap -Value (Get-LauncherProperty $Value "environment")
    $clearInherited = Get-LauncherProperty $Value "clear_inherited_environment"
    if ($clearInherited -isnot [bool] -or -not $clearInherited) { throw "launcher_private_plan_invalid" }
    $listenerPort = Get-LauncherProperty $Value "listener_port"
    if ($listenerPort -isnot [int] -and $listenerPort -isnot [long]) { throw "launcher_private_plan_invalid" }
    if ([int64]$listenerPort -ne [int64]$Descriptor.DefaultListenerPort) { throw "launcher_private_plan_invalid" }
    if ($serviceId -ceq "aituber_kit") {
        Assert-LauncherAituberPlan `
            -FilePath $filePath `
            -Arguments $arguments `
            -WorkingDirectory $workingDirectory `
            -ListenerPort ([int]$listenerPort)
    }
    return [pscustomobject]@{
        ServiceId = $serviceId
        Requirement = [string]$Descriptor.Requirement
        Ownership = "owned"
        FilePath = $filePath
        Arguments = @($arguments)
        WorkingDirectory = $workingDirectory
        Environment = $environment
        RemoveEnvironment = @($removeEnvironment)
        ClearInheritedEnvironment = [bool]$clearInherited
        ListenerPort = [int]$listenerPort
    }
}

function Read-LauncherPrivateServicePlans {
    param([Parameter(Mandatory = $true)][string]$Path)
    if (-not [IO.Path]::IsPathFullyQualified($Path)) { throw "launcher_private_plan_invalid" }
    $item = Get-Item -LiteralPath $Path -Force -ErrorAction Stop
    if (-not $item.PSIsContainer -and ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0 -and $item.Length -le $script:MaximumPlanBytes) {
        $bytes = [IO.File]::ReadAllBytes($item.FullName)
    }
    else { throw "launcher_private_plan_invalid" }
    $strictUtf8 = [Text.UTF8Encoding]::new($false, $true)
    try { $text = $strictUtf8.GetString($bytes) } catch { throw "launcher_private_plan_invalid" }
    if ($text.StartsWith([char]0xFEFF)) { throw "launcher_private_plan_invalid" }
    try { $document = $text | ConvertFrom-Json -Depth 32 -ErrorAction Stop } catch { throw "launcher_private_plan_invalid" }
    Assert-LauncherExactKeys -InputObject $document -Expected @(
        "schema_version", "graph_sha256", "binding_sha256", "profile_id",
        "effective_config_sha256", "camera_policy", "worker_file_path", "services"
    )
    if ((Get-LauncherProperty $document "schema_version") -cne "launcher_private_service_plans.v1") { throw "launcher_private_plan_invalid" }
    $graphSha256 = Assert-LauncherBoundedString -Value (Get-LauncherProperty $document "graph_sha256") -Maximum 64
    $bindingSha256 = Assert-LauncherBoundedString -Value (Get-LauncherProperty $document "binding_sha256") -Maximum 64
    $profileId = Assert-LauncherBoundedString -Value (Get-LauncherProperty $document "profile_id") -Maximum 64
    $effectiveConfigSha256 = Assert-LauncherBoundedString -Value (Get-LauncherProperty $document "effective_config_sha256") -Maximum 64
    $cameraPolicy = Assert-LauncherBoundedString -Value (Get-LauncherProperty $document "camera_policy") -Maximum 64
    $workerFilePath = Assert-LauncherBoundedString -Value (Get-LauncherProperty $document "worker_file_path") -Maximum 1024
    if ($graphSha256 -cnotmatch $script:Sha256Pattern -or $bindingSha256 -cnotmatch $script:Sha256Pattern -or
        $effectiveConfigSha256 -cnotmatch $script:Sha256Pattern -or $profileId -cne "thought-core-v0" -or
        @("required", "camera_excluded_by_profile") -cnotcontains $cameraPolicy -or
        -not [IO.Path]::IsPathFullyQualified($workerFilePath) -or
        @("pwsh", "powershell") -cnotcontains [IO.Path]::GetFileNameWithoutExtension($workerFilePath).ToLowerInvariant()) {
        throw "launcher_private_plan_invalid"
    }
    $servicesValue = Get-LauncherProperty $document "services"
    if ($servicesValue -isnot [System.Collections.IEnumerable] -or $servicesValue -is [string]) { throw "launcher_private_plan_invalid" }
    $plans = @{}
    foreach ($service in @($servicesValue)) {
        $serviceId = Assert-LauncherBoundedString -Value (Get-LauncherProperty $service "service_id") -Maximum 64
        if ($plans.ContainsKey($serviceId)) { throw "launcher_private_plan_invalid" }
        $descriptor = Get-LauncherServiceDescriptor -ServiceId $serviceId
        if ($descriptor.Ownership -ne "owned") { throw "launcher_private_plan_invalid" }
        $plans[$serviceId] = ConvertTo-LauncherOwnedPlan -Value $service -Descriptor $descriptor
    }
    foreach ($descriptor in $script:Descriptors.Values) {
        if ($descriptor.Ownership -eq "owned" -and $descriptor.Requirement -eq "required" -and -not $plans.ContainsKey($descriptor.ServiceId)) {
            throw "launcher_private_plan_invalid"
        }
    }
    return [pscustomobject]@{
        SchemaVersion = "launcher_private_service_plans.v1"
        GraphSha256 = $graphSha256
        BindingSha256 = $bindingSha256
        ProfileId = $profileId
        EffectiveConfigSha256 = $effectiveConfigSha256
        CameraPolicy = $cameraPolicy
        WorkerFilePath = $workerFilePath
        Plans = $plans
    }
}

function Resolve-LauncherServicePlan {
    param(
        [Parameter(Mandatory = $true)][object]$PlanSet,
        [Parameter(Mandatory = $true)][string]$ServiceId,
        [Parameter(Mandatory = $true)][string]$GraphSha256,
        [Parameter(Mandatory = $true)][string]$BindingSha256
    )
    $descriptor = Get-LauncherServiceDescriptor -ServiceId $ServiceId
    if ($PlanSet.GraphSha256 -cne $GraphSha256 -or $PlanSet.BindingSha256 -cne $BindingSha256) {
        throw "launcher_private_plan_identity_mismatch"
    }
    if ($descriptor.Ownership -eq "external") {
        return [pscustomobject]@{
            ServiceId = $descriptor.ServiceId
            Requirement = $descriptor.Requirement
            Ownership = $descriptor.Ownership
            ListenerPort = $descriptor.DefaultListenerPort
        }
    }
    if (-not $PlanSet.Plans.ContainsKey($ServiceId)) {
        if ($descriptor.Requirement -eq "optional") { return $null }
        throw "launcher_private_plan_missing"
    }
    return $PlanSet.Plans[$ServiceId]
}

Export-ModuleMember -Function @(
    "Get-LauncherServiceIds",
    "Get-LauncherServiceDescriptor",
    "Read-LauncherPrivateServicePlans",
    "Resolve-LauncherServicePlan"
)
