$ErrorActionPreference = 'Stop'
Set-Location (Split-Path $PSScriptRoot -Parent)
function Run-Step([scriptblock]$Command) {
    & $Command
    if ($LASTEXITCODE -ne 0) { throw "Command failed with exit code $LASTEXITCODE" }
}
if (-not (Test-Path .venv/Scripts/python.exe)) { Run-Step { py -3.12 -m venv .venv } }
Run-Step { .venv/Scripts/python.exe -m pip install uv }
Run-Step { .venv/Scripts/uv.exe sync --locked --inexact }
Run-Step { npm.cmd --prefix frontend ci }
Run-Step { npm.cmd --prefix frontend run build }
Write-Host 'Ready. Run .venv/Scripts/python.exe -m c3_mass --open'
