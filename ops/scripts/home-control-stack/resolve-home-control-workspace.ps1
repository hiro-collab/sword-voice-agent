function Resolve-HomeControlWorkspaceRoot {
    param(
        [string]$WorkspaceRoot = "",
        [string]$ScriptRoot = $PSScriptRoot
    )

    if (-not [string]::IsNullOrWhiteSpace($WorkspaceRoot)) {
        return (Resolve-Path -LiteralPath $WorkspaceRoot).Path
    }

    if (-not [string]::IsNullOrWhiteSpace($env:HOME_CONTROL_WORKSPACE_ROOT)) {
        return (Resolve-Path -LiteralPath $env:HOME_CONTROL_WORKSPACE_ROOT).Path
    }

    $candidates = @(
        (Join-Path $ScriptRoot "..\..\..\.."),
        (Join-Path $ScriptRoot "..\..\.."),
        (Join-Path $ScriptRoot "..")
    )

    foreach ($candidate in $candidates) {
        $resolved = Resolve-Path -LiteralPath $candidate -ErrorAction SilentlyContinue
        if ($null -eq $resolved) {
            continue
        }

        $path = $resolved.Path
        $hasControlPlaneLayout = (
            (Test-Path -LiteralPath (Join-Path $path "control-plane\core") -PathType Container)
        )
        $hasSystemCellLayout = (
            (Test-Path -LiteralPath (Join-Path $path "organs\expression\aituber-kit") -PathType Container) -and
            (Test-Path -LiteralPath (Join-Path $path "organs\action\home-assistant-server") -PathType Container) -and
            $hasControlPlaneLayout
        )
        if ($hasSystemCellLayout) {
            return $path
        }
    }

    throw "Unable to resolve Home Control workspace root. Pass -WorkspaceRoot explicitly."
}
