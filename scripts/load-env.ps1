param(
    [string]$Path = ".env"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

if (-not (Test-Path -LiteralPath $Path)) {
    throw "env file not found: $Path"
}

$count = 0
foreach ($line in Get-Content -LiteralPath $Path) {
    $trimmed = $line.Trim()
    if ($trimmed.Length -eq 0 -or $trimmed.StartsWith("#")) {
        continue
    }
    if ($trimmed.StartsWith("export ")) {
        $trimmed = $trimmed.Substring(7).Trim()
    }

    $separator = $trimmed.IndexOf("=")
    if ($separator -lt 1) {
        throw "invalid env line: $line"
    }

    $key = $trimmed.Substring(0, $separator).Trim()
    $value = $trimmed.Substring($separator + 1).Trim()
    if ($key -notmatch "^[A-Za-z_][A-Za-z0-9_]*$") {
        throw "invalid env key: $key"
    }

    if (
        ($value.StartsWith('"') -and $value.EndsWith('"')) -or
        ($value.StartsWith("'") -and $value.EndsWith("'"))
    ) {
        $value = $value.Substring(1, $value.Length - 2)
    }

    $existing = [Environment]::GetEnvironmentVariable($key, "Process")
    if (
        [string]::IsNullOrWhiteSpace($value) -and
        -not [string]::IsNullOrWhiteSpace($existing)
    ) {
        continue
    }

    Set-Item -Path "Env:$key" -Value $value
    $count += 1
}

Write-Host "loaded $count env vars from $Path"
