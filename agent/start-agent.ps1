<#
.SYNOPSIS
Start the Device Agent (WS register + heartbeat + execution).

.DESCRIPTION
- Adds uv to PATH if missing.
- Runs `uv run python main.py --config <config>`.
- Default config: config.yaml in the agent directory.

.EXAMPLE
.\start-agent.ps1
.\start-agent.ps1 -Config my-agent.yaml
#>

param(
    [string]$Config = "config.yaml"
)

$ErrorActionPreference = "Stop"
$AgentDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$uvPath = Join-Path $env:USERPROFILE ".local\bin"
if (Test-Path (Join-Path $uvPath "uv.exe")) {
    $env:Path = "$uvPath;" + $env:Path
} else {
    Write-Warning "uv not found at $uvPath"
}

Set-Location $AgentDir
Write-Host "[start] main.py --config $Config"
uv run python main.py --config $Config
