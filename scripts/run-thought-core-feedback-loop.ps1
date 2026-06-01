param(
    [int]$Count = 1,
    [string]$Python = ".\.venv\Scripts\python.exe"
)

$ErrorActionPreference = "Stop"
Set-StrictMode -Version Latest
. (Join-Path $PSScriptRoot "common.ps1")

$repoRoot = Get-SwordRepoRoot
$pythonPath = Resolve-SwordPath -Path $Python
if (-not (Test-Path -LiteralPath $pythonPath -PathType Leaf)) {
    throw "Python executable not found: $pythonPath"
}

$runs = [Math]::Max(1, $Count)
for ($index = 1; $index -le $runs; $index++) {
    Write-Host ("thought-core feedback loop replay {0}/{1}" -f $index, $runs)
    & $pythonPath -m unittest tests.test_thought_core_feedback_loop
    if ($LASTEXITCODE -ne 0) {
        throw "thought-core feedback loop replay failed at run $index"
    }
}

Write-Host ("thought-core feedback loop replay passed x{0}" -f $runs)
