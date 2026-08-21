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
$vendor = "vendor"
if (Test-Path "$vendor\platform-tools\adb.exe") {
    Write-Host "== 2) copy platform-tools =="
    Copy-Item -Recurse -Force "$vendor\platform-tools" "dist\app-auto-test-agent\platform-tools"
} else {
    Write-Host "== 2) vendor\platform-tools not found; installer will NOT bundle adb =="
}

# 3) Inno Setup compile
Write-Host "== 3) Inno Setup =="
$iscc = $null
$cmd = Get-Command iscc -ErrorAction SilentlyContinue
if ($cmd) {
    $iscc = $cmd.Source
} else {
    $candidates = @(
        "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
        "${env:ProgramFiles}\Inno Setup 6\ISCC.exe"
    )
    foreach ($candidate in $candidates) {
        if (Test-Path $candidate) { $iscc = $candidate; break }
    }
}
if (-not $iscc) { throw "iscc (Inno Setup 6) not found; install Inno Setup 6 first" }

& $iscc "packaging\setup.iss" "/DAppVersion=$Version" "/DOutputDir=dist"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup compile failed" }

# 4) SHA-256 + manifest (ASCII manifest: backend reads it with json.loads, no BOM allowed)
Write-Host "== 4) manifest =="
$exe = "dist\app-auto-test-agent-$Version-windows-x64-setup.exe"
if (-not (Test-Path $exe)) { throw "installer not found: $exe" }
$hash = (Get-FileHash -Algorithm SHA256 $exe).Hash.ToLowerInvariant()
$size = (Get-Item $exe).Length
New-Item -ItemType Directory -Force -Path $ReleaseDir | Out-Null
$manifest = @{
    version      = $Version
    filename     = (Split-Path $exe -Leaf)
    sha256       = $hash
    size         = $size
    published_at = (Get-Date).ToUniversalTime().ToString("o")
}
# atomic update: write tmp then rename
$tmpManifest = Join-Path $ReleaseDir "latest.json.tmp"
$manifest | ConvertTo-Json | Set-Content -Encoding ASCII $tmpManifest
Move-Item -Force $tmpManifest (Join-Path $ReleaseDir "latest.json")

# 5) copy installer
Copy-Item -Force $exe $ReleaseDir
Write-Host "== done =="
Write-Host "installer: $ReleaseDir\$(Split-Path $exe -Leaf)"
Write-Host "SHA-256: $hash"
