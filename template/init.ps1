# init.ps1 — Verificación e inicialización del entorno (Windows PowerShell).
#
# Equivalente a init.sh. La lógica vive en tools/harness_check.py.

Set-Location -Path $PSScriptRoot

foreach ($candidate in @("python", "py", "python3")) {
    $cmd = Get-Command $candidate -ErrorAction SilentlyContinue
    if ($null -ne $cmd) {
        & $candidate -c "import sys" *> $null
        if ($LASTEXITCODE -eq 0) {
            & $candidate tools/harness_check.py @args
            exit $LASTEXITCODE
        }
    }
}

Write-Output "[FAIL]  No se encontró ningún intérprete de Python (python, py, python3)"
exit 1
