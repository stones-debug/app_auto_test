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

# PyInstaller / Inno outputs are fixed under packaging\ (spec/iss location), independent of CWD
$packagingDir = $PSScriptRoot
$distDir = Join-Path $packagingDir "dist"
$workDir = Join-Path $packagingDir "build"
$bundledApp = Join-Path $distDir "app-auto-test-agent"
$installer = Join-Path $distDir "app-auto-test-agent-$Version-windows-x64-setup.exe"

# 0) self-check
Write-Host "== 0) Agent self-check (config: $configFile) =="
uv run python main.py --config $configFile --self-check
if ($LASTEXITCODE -ne 0) { throw "self-check failed" }

if (-not $SkipBuild) {
    # 1) PyInstaller onedir (explicit distpath/workpath so outputs never depend on CWD)
    Write-Host "== 1) PyInstaller =="
    # `uv run` re-syncs the env WITHOUT extras by default, which drops pystray/PIL
    # (desktop tray). Sync with extras first so the bundle contains them.
    uv sync --all-extras
    if ($LASTEXITCODE -ne 0) { throw "uv sync --all-extras failed" }
    uv run --with pyinstaller pyinstaller packaging\pyinstaller.spec --noconfirm `
        --distpath $distDir --workpath $workDir
    if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
}

# 2) bundle platform-tools if present
$vendor = "vendor"
if (Test-Path "$vendor\platform-tools\adb.exe") {
    Write-Host "== 2) copy platform-tools =="
    $ptTarget = Join-Path $bundledApp "platform-tools"
    if (Test-Path $ptTarget) { Remove-Item -Recurse -Force $ptTarget }
    Copy-Item -Recurse -Force "$vendor\platform-tools" $ptTarget
} else {
    Write-Host "== 2) vendor\platform-tools not found; installer will NOT bundle adb =="
}

# 2.5) bundle portable Node + Appium Server + UiAutomator2 driver (Windows plan 4.1)
$appiumVendor = "$vendor\appium"
$nodeVersion = "v20.20.2"
$appiumReady = (Test-Path "$appiumVendor\node\node.exe") -and `
    (Test-Path "$appiumVendor\appium\node_modules\appium\build\lib\main.js") -and `
    (Test-Path "$appiumVendor\appium-home\node_modules")
if (-not $appiumReady) {
    Write-Host "== 2.5) preparing portable Node $nodeVersion + Appium =="
    if (Test-Path $appiumVendor) { Remove-Item -Recurse -Force $appiumVendor }
    New-Item -ItemType Directory -Force -Path $appiumVendor | Out-Null
    $nodeZip = Join-Path $env:TEMP "node-$nodeVersion-win-x64.zip"
    $nodeExtract = Join-Path $env:TEMP "node-$nodeVersion-extract"
    if (-not (Test-Path $nodeZip)) {
        Invoke-WebRequest -Uri "https://nodejs.org/dist/$nodeVersion/node-$nodeVersion-win-x64.zip" -OutFile $nodeZip
    }
    if (Test-Path $nodeExtract) { Remove-Item -Recurse -Force $nodeExtract }
    Expand-Archive -Force $nodeZip $nodeExtract
    Copy-Item -Recurse -Force "$nodeExtract\node-$nodeVersion-win-x64" "$appiumVendor\node"
    $nodeExe = Join-Path $appiumVendor "node\node.exe"
    $npmCli = Join-Path $appiumVendor "node\node_modules\npm\bin\npm-cli.js"
    & $nodeExe $npmCli install --prefix "$appiumVendor\appium" --no-audit --no-fund appium@2
    if ($LASTEXITCODE -ne 0) { throw "appium npm install failed" }
    $appiumHome = Join-Path $appiumVendor "appium-home"
    $appiumMain = Join-Path $appiumVendor "appium\node_modules\appium\build\lib\main.js"
    $oldHome = $env:APPIUM_HOME
    $env:APPIUM_HOME = $appiumHome
    try {
        # Pin uiautomator2 3.9.1: newer 7.x/8.x require Appium 3 RC (peer ^3.0.0-rc.2)
        & $nodeExe $appiumMain driver install "uiautomator2@3.9.1"
        if ($LASTEXITCODE -ne 0) { throw "uiautomator2 driver install failed" }
    } finally {
        if ($null -eq $oldHome) { Remove-Item Env:\APPIUM_HOME } else { $env:APPIUM_HOME = $oldHome }
    }
    Write-Host "== 2.5) portable Node + Appium ready =="
}
if (Test-Path "$appiumVendor\node\node.exe") {
    Write-Host "== 2.5) copy portable Node + Appium =="
    $appiumTarget = Join-Path $bundledApp "appium"
    if (Test-Path $appiumTarget) { Remove-Item -Recurse -Force $appiumTarget }
    Copy-Item -Recurse -Force $appiumVendor $appiumTarget
    # Appium 首次启动会按运行时 APPIUM_HOME 重新生成 extensions.yaml；
    # 删除构建机残留 manifest，避免 installPath 指向构建机绝对路径
    $staleManifest = Join-Path $appiumTarget "appium-home\node_modules\.cache"
    if (Test-Path $staleManifest) { Remove-Item -Recurse -Force $staleManifest }
} else {
    Write-Host "== 2.5) vendor\appium not found; installer will NOT bundle Appium =="
}

# 2.6) bundle Python dist-info metadata (appium/selenium read own version via importlib.metadata)
$metaTarget = Join-Path $bundledApp "_internal"
foreach ($pat in @("appium_python_client-*.dist-info", "selenium-*.dist-info")) {
    Get-ChildItem ".venv\Lib\site-packages" -Directory -Filter $pat -ErrorAction SilentlyContinue | ForEach-Object {
        Write-Host "== 2.6) copy $($_.Name) =="
        Copy-Item -Recurse -Force $_.FullName (Join-Path $metaTarget $_.Name)
    }
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
