<#
.SYNOPSIS
Start the Task Worker (queue consumer + scheduler scans).

.DESCRIPTION
- Optional external-mode compatibility entrypoint.
- Requires WORKER_MODE=external in backend/.env.
- Set WORKER_CONCURRENCY in backend/.env to control concurrent executions (default: 1).
- Runs `uv run python worker.py --worker-id <id> --enable-scans`.

.EXAMPLE
.\start-worker.ps1
.\start-worker.ps1 -WorkerId worker-002
#>

param(
    [string]$WorkerId = "worker-001"
)

$ErrorActionPreference = "Stop"
$BackendDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$uvPath = Join-Path $env:USERPROFILE ".local\bin"
if (Test-Path (Join-Path $uvPath "uv.exe")) {
    $env:Path = "$uvPath;" + $env:Path
} else {
    Write-Warning "uv not found at $uvPath"
}

Set-Location $BackendDir
Write-Host "[start] external worker.py --worker-id $WorkerId --enable-scans"
Write-Host "[hint] backend/.env must contain WORKER_MODE=external"
uv run python worker.py --worker-id $WorkerId --enable-scans
