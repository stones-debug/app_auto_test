# Windows Agent 安装包发布脚本（Windows 方案 §4.2/§3.4）
# 用法: powershell -ExecutionPolicy Bypass -File packaging\publish.ps1 -Version 1.1.0 -ReleaseDir ..\backend\data\agent-releases
# 流程: 自检 -> PyInstaller onedir -> Inno Setup -> SHA-256 -> latest.json 原子更新 -> 复制到后端发布目录
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

# 0) 自检
Write-Host "== 0) Agent self-check =="
uv run python main.py --config config.yaml --self-check
if ($LASTEXITCODE -ne 0) { throw "self-check 失败" }

if (-not $SkipBuild) {
    # 1) PyInstaller onedir
    Write-Host "== 1) PyInstaller =="
    uv run --with pyinstaller pyinstaller packaging\pyinstaller.spec --noconfirm
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller 失败" }
}

# 2) 内嵌 platform-tools（若存在 vendor 目录）
$vendor = "vendor"
if (Test-Path "$vendor\platform-tools\adb.exe") {
    Write-Host "== 2) 复制 platform-tools =="
    Copy-Item -Recurse -Force "$vendor\platform-tools" "dist\app-auto-test-agent\platform-tools"
} else {
    Write-Host "== 2) 未找到 vendor\platform-tools，安装包不含 adb（需目标机另行安装） =="
}

# 3) Inno Setup 编译
Write-Host "== 3) Inno Setup =="
$iscc = Get-Command iscc -ErrorAction SilentlyContinue
if (-not $iscc) {
    $isccPath = "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe"
    if (Test-Path $isccPath) { $iscc = $isccPath } else { throw "未找到 iscc（Inno Setup 6）" }
} else {
    $iscc = $iscc.Source
}
& $iscc "packaging\setup.iss" "/DAppVersion=$Version" "/DOutputDir=dist"
if ($LASTEXITCODE -ne 0) { throw "Inno Setup 编译失败" }

# 4) SHA-256 + manifest
Write-Host "== 4) manifest =="
$exe = "dist\app-auto-test-agent-$Version-windows-x64-setup.exe"
if (-not (Test-Path $exe)) { throw "未找到安装包: $exe" }
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
# 原子更新 latest.json（先写临时文件再 rename）
$tmpManifest = Join-Path $ReleaseDir "latest.json.tmp"
$manifest | ConvertTo-Json | Set-Content -Encoding UTF8 $tmpManifest
Move-Item -Force $tmpManifest (Join-Path $ReleaseDir "latest.json")

# 5) 复制安装包
Copy-Item -Force $exe $ReleaseDir
Write-Host "== 完成 =="
Write-Host "安装包: $ReleaseDir\$(Split-Path $exe -Leaf)"
Write-Host "SHA-256: $hash"
