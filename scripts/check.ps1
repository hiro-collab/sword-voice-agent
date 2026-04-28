$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest

$repoRoot = Split-Path -Parent $PSScriptRoot
Push-Location $repoRoot
try {
    $env:PYTHONPATH = "src"

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
