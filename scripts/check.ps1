$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $env:PYTHONPATH = "src"

    $scriptFiles = Get-ChildItem -Path "scripts" -Filter "*.ps1"
    foreach ($scriptFile in $scriptFiles) {
        $tokens = $null
        $parseErrors = $null
        [System.Management.Automation.Language.Parser]::ParseFile(
            $scriptFile.FullName,
            [ref]$tokens,
            [ref]$parseErrors
        ) | Out-Null

        if ($parseErrors.Count -gt 0) {
            $messages = ($parseErrors | ForEach-Object { $_.Message }) -join "; "
            throw "PowerShell parse failed in $($scriptFile.Name): $messages"
        }
    }

    $pythonFiles = Get-ChildItem -Path "src", "tests" -Recurse -Filter "*.py" |
        Select-Object -ExpandProperty FullName

    if ($pythonFiles.Count -gt 0) {
        python -m py_compile @pythonFiles
    }

    python -m unittest discover -s tests
}
finally {
    Pop-Location
}
