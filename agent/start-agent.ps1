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
.\start-agent.ps1 -AndroidSdkRoot "C:\\Users\\<user>\\AppData\\Local\\Android\\Sdk"
#>

param(
    [string]$Config = "config.yaml",
    [string]$AndroidSdkRoot = ""
)

$ErrorActionPreference = "Stop"
$AgentDir = Split-Path -Parent $MyInvocation.MyCommand.Path

$uvPath = Join-Path $env:USERPROFILE ".local\bin"
if (Test-Path (Join-Path $uvPath "uv.exe")) {
    $env:Path = "$uvPath;" + $env:Path
} else {
    Write-Warning "uv not found at $uvPath"
}

function Resolve-AndroidSdkRoot([string]$RequestedRoot) {
    $candidates = New-Object System.Collections.Generic.List[string]
    if (-not [string]::IsNullOrWhiteSpace($RequestedRoot)) {
        [void]$candidates.Add($RequestedRoot)
    }
    foreach ($name in @("ANDROID_HOME", "ANDROID_SDK_ROOT")) {
        $value = [Environment]::GetEnvironmentVariable($name)
        if (-not [string]::IsNullOrWhiteSpace($value)) {
            [void]$candidates.Add($value)
        }
    }

    $adb = Get-Command adb.exe -ErrorAction SilentlyContinue
    if ($null -ne $adb -and -not [string]::IsNullOrWhiteSpace($adb.Source)) {
        $adbPath = [IO.Path]::GetFullPath($adb.Source)
        $platformTools = Split-Path -Parent $adbPath
        if ((Split-Path -Leaf $platformTools) -ieq "platform-tools") {
            [void]$candidates.Add((Split-Path -Parent $platformTools))
        }
    }

    if (-not [string]::IsNullOrWhiteSpace($env:LOCALAPPDATA)) {
        [void]$candidates.Add((Join-Path $env:LOCALAPPDATA "Android\Sdk"))
    }
    if (-not [string]::IsNullOrWhiteSpace($env:USERPROFILE)) {
        [void]$candidates.Add((Join-Path $env:USERPROFILE "AppData\Local\Android\Sdk"))
    }
    # 源码/开发包随附的 adb 位于 agent\vendor\platform-tools\adb.exe，
    # 因此 Android SDK 根目录是 agent\vendor。
    [void]$candidates.Add((Join-Path $AgentDir "vendor"))

    foreach ($candidate in $candidates) {
        $expanded = [Environment]::ExpandEnvironmentVariables($candidate.Trim().Trim('"'))
        if ((Test-Path -LiteralPath $expanded -PathType Container) -and
            (Test-Path -LiteralPath (Join-Path $expanded "platform-tools\adb.exe") -PathType Leaf)) {
            return (Resolve-Path -LiteralPath $expanded).Path
        }
    }
    return $null
}

$sdkRoot = Resolve-AndroidSdkRoot $AndroidSdkRoot
if ($null -ne $sdkRoot) {
    $env:ANDROID_HOME = $sdkRoot
    $env:ANDROID_SDK_ROOT = $sdkRoot
    $platformTools = Join-Path $sdkRoot "platform-tools"
    $emulator = Join-Path $sdkRoot "emulator"
    $env:Path = "$platformTools;$emulator;" + $env:Path
    Write-Host "[start] Android SDK: $sdkRoot"
} elseif (-not [string]::IsNullOrWhiteSpace($AndroidSdkRoot)) {
    throw "Android SDK 路径无效或缺少 platform-tools\adb.exe: $AndroidSdkRoot"
} else {
    Write-Warning "未找到 Android SDK。Appium 需要设置 ANDROID_HOME/ANDROID_SDK_ROOT，或使用 -AndroidSdkRoot 指定 SDK 路径。"
}

Set-Location $AgentDir
Write-Host "[start] main.py --config $Config"
uv run python main.py --config $Config
