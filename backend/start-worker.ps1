<#
.SYNOPSIS
Start the Task Worker (queue consumer + scheduler scans).

.DESCRIPTION
- Adds uv to PATH if missing.
- Runs `uv run python worker.py --worker-id <id> --enable-scans`.
- Scans (reclaim/timeout/heartbeat/daily-cleanup) only enabled here.

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
Write-Host "[start] worker.py --worker-id $WorkerId --enable-scans"
uv run python worker.py --worker-id $WorkerId --enable-scans
