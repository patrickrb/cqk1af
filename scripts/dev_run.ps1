# Boots the MCP server, dashboard backend, and Vite dev server.
# Phase 3+ will fill this in.
param(
    [switch]$MockRadio
)

$ErrorActionPreference = "Stop"

Push-Location (Split-Path $PSScriptRoot -Parent)
try {
    if (-not (Test-Path ".venv")) {
        Write-Host "Creating virtualenv..."
        # Prefer 3.11; fall back to whatever 3.x py launcher picks.
        $pyExe = (Get-Command py -ErrorAction SilentlyContinue).Source
        if ($pyExe) {
            & py -3.11 -m venv .venv 2>$null
            if (-not (Test-Path ".venv")) { & py -3 -m venv .venv }
        } else {
            & python -m venv .venv
        }
        & .\.venv\Scripts\python.exe -m pip install --upgrade pip
        & .\.venv\Scripts\python.exe -m pip install -e ".[dev]"
    }

    if ($MockRadio) { $env:CQK1AF_RADIO__MOCK_MODE = "true" }

    Write-Host "Starting cqk1af (placeholder; Phase 3+ to launch services)..."
    & .\.venv\Scripts\python.exe -m cqk1af hello
} finally {
    Pop-Location
}
