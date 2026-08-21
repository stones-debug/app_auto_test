# Windows Agent installer publish script (Windows plan 4.2 / 3.4)
# ASCII-only on purpose: PowerShell 5.1 parses .ps1 as ANSI(GBK) when there is no BOM,
# so non-ASCII comments/messages would corrupt parsing. Keep this file ASCII.
#
# Usage:
#   powershell -ExecutionPolicy Bypass -File packaging\publish.ps1 -Version 1.1.0 -ReleaseDir ..\backend\data\agent-releases
#
# Flow: self-check -> PyInstaller onedir -> copy vendor platform-tools -> Inno Setup -> SHA-256 -> latest.json (atomic) -> copy to release dir
param(
    [string]$Version = "1.1.0",
    [string]$ReleaseDir = "",
    [switch]$SkipBuild
)

$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")

if (-not $ReleaseDir) {
    $ReleaseDir = Join-Path $PSScriptRoot "..\..\backend\data\agent-releases"
}

$configFile = "config.yaml"
if (-not (Test-Path $configFile)) {
    $configFile = "config.yaml.example"
}

# 0) self-check
Write-Host "== 0) Agent self-check (config: $configFile) =="
uv run python main.py --config $configFile --self-check
if ($LASTEXITCODE -ne 0) { throw "self-check failed" }

if (-not $SkipBuild) {
    # 1) PyInstaller onedir
    Write-Host "== 1) PyInstaller =="
    uv run --with pyinstaller pyinstaller packaging\pyinstaller.spec --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
}

# 2) bundle platform-tools if present
$packagingDir = $PSScriptRoot
# PyInstaller(基于 spec 文件)与 Inno(基于 iss 文件)的输出目录都相对 packaging\ 解析
$distDir = Join-Path $packagingDir "dist"
$bundledApp = Join-Path $distDir "app-auto-test-agent"
$installer = Join-Path $distDir "app-auto-test-agent-$Version-windows-x64-setup.exe"

$vendor = "vendor"
if (Test-Path "$vendor\platform-tools\adb.exe") {
    Write-Host "== 2) copy platform-tools =="
    Copy-Item -Recurse -Force "$vendor\platform-tools" (Join-Path $bundledApp "platform-tools")
} else {
    Write-Host "== 2) vendor\platform-tools not found; installer will NOT bundle adb =="
}

# 3) Inno Setup compile
Write-Host "== 3) Inno Setup =="

function Find-Iscc {
    # 1) PATH
    $cmd = Get-Command iscc -ErrorAction SilentlyContinue
    if ($cmd) { return $cmd.Source }
    # 2) registry uninstall entry (works for non-default install dirs, e.g. D:\Program Files\Inno Setup 6)
    $uninstall = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKCU:\SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    $location = Get-ItemProperty $uninstall -ErrorAction SilentlyContinue |
        Where-Object { $_.DisplayName -like '*Inno Setup*' -and $_.InstallLocation } |
        Select-Object -First 1 -ExpandProperty InstallLocation
    if ($location) {
        $candidate = Join-Path $location 'ISCC.exe'
        if (Test-Path $candidate) { return $candidate }
    }
    # 3) common paths
    foreach ($candidate in @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles(x86)}\Inno Setup 7\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 7\ISCC.exe"
    )) {
        if (Test-Path $candidate) { return $candidate }
    }
    return $null
}

$iscc = Find-Iscc
if (-not $iscc) { throw "iscc (Inno Setup 6) not found; install Inno Setup 6 first" }
Write-Host "using iscc: $iscc"

& $iscc "packaging\setup.iss" "/DAppVersion=$Version" "/DOutputDir=$distDir"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }

# 4) SHA-256 + manifest (ASCII manifest: backend reads it with json.loads, no BOM allowed)
Write-Host "== 4) manifest =="
if (-not (Test-Path $installer)) { throw "installer not found: $installer" }
$hash = (Get-FileHash -Algorithm SHA256 $installer).Hash.ToLowerInvariant()
$size = (Get-Item $installer).Length
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
$manifest = @{
    version      = $Version
    filename     = (Split-Path $installer -Leaf)
    sha256       = $hash
    size         = $size
    published_at = (Get-Date).ToUniversalTime().ToString("o")
}
# atomic update: write tmp then rename
$tmpManifest = Join-Path $ReleaseDir "latest.json.tmp"
$manifest | ConvertTo-Json | Set-Content -Encoding ASCII $tmpManifest
Move-Item -Force $tmpManifest (Join-Path $ReleaseDir "latest.json")

# 5) copy installer
Copy-Item -Force $installer $ReleaseDir
Write-Host "== done =="
Write-Host "installer: $ReleaseDir\$(Split-Path $installer -Leaf)"
Write-Host "SHA-256: $hash"
