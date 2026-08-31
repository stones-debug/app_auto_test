<#
.SYNOPSIS
部署并启动 APP 自动化测试平台后端。

.DESCRIPTION
适用于 Windows + 原生 PostgreSQL 的单机部署：
  1. 检查 Python、requirements.txt 和 .env；
  2. 用 Python 虚拟环境和 pip 安装依赖；
  3. 创建报告、Agent 发布包和运行时目录；
  4. 执行 Alembic 迁移及迁移漂移检查；
  5. 可选创建默认 admin 账号；
  6. 以单个 Uvicorn 进程启动 FastAPI（V1 WebSocket 要求单进程）；
  7. WORKER_MODE=external 时额外启动一个独立 Worker；
  8. 写入 PID/日志并等待 /api/health 成功。

生产部署前必须自行准备 backend/.env，并使用随机强密钥。脚本不会覆盖已有 .env，
也不会自动生成生产密钥或修改数据库密码。

.EXAMPLE
  # 首次生产部署：迁移、启动 embedded Worker，不创建默认账号
  powershell -ExecutionPolicy Bypass -File .\deploy.ps1

  # 初始化 admin/admin123（首次登录后必须立即改密）
  powershell -ExecutionPolicy Bypass -File .\deploy.ps1 -Seed

  # 只安装依赖、迁移和校验，不启动进程
  powershell -ExecutionPolicy Bypass -File .\deploy.ps1 -NoStart

  # external Worker 部署（.env 中 WORKER_MODE=external）
  powershell -ExecutionPolicy Bypass -File .\deploy.ps1 -WorkerId worker-001

  # 重启脚本管理的后端/Worker
  powershell -ExecutionPolicy Bypass -File .\deploy.ps1 -Restart
#>

[CmdletBinding()]
param(
    [ValidateSet("development", "production")]
    [string]$Environment = "production",
    [ValidateRange(1, 65535)]
    [int]$Port = 8001,
    [string]$ListenHost = "0.0.0.0",
    [string]$WorkerId = "",
    [switch]$Seed,
    [Alias("SkipSync")]
    [switch]$SkipInstall,
    [switch]$SkipMigrate,
    [switch]$NoStart,
    [switch]$Restart,
    [switch]$StopOnly,
    [int]$HealthTimeoutSeconds = 60
)

$ErrorActionPreference = "Stop"
$BackendDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$DataDir = Join-Path $BackendDir "data"
$RuntimeDir = Join-Path $DataDir "runtime"
$LogDir = Join-Path $DataDir "logs"
$BackendPidFile = Join-Path $RuntimeDir "backend.pid"
$WorkerPidFile = Join-Path $RuntimeDir "worker.pid"
$BackendStdout = Join-Path $LogDir "backend.out.log"
$BackendStderr = Join-Path $LogDir "backend.err.log"
$WorkerStdout = Join-Path $LogDir "worker.out.log"
$WorkerStderr = Join-Path $LogDir "worker.err.log"

function Write-Step([string]$Message) {
    Write-Host "[deploy] $Message" -ForegroundColor Cyan
}

function Stop-ManagedProcess([string]$PidFile, [string]$Name) {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        return
    }
    $rawPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    $processId = 0
    if ([int]::TryParse($rawPid, [ref]$processId) -and $processId -gt 0) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            # PID 文件可能来自已删除/重装的部署。先核对进程路径和命令行，
            # 避免 PID 被系统复用后误停止无关或受保护的进程。
            $processInfo = Get-CimInstance Win32_Process -Filter "ProcessId = $processId" -ErrorAction SilentlyContinue
            $expectedPython = Join-Path $BackendDir ".venv\Scripts\python.exe"
            $executablePath = if ($null -ne $processInfo) { [string]$processInfo.ExecutablePath } else { "" }
            $commandLine = if ($null -ne $processInfo) { [string]$processInfo.CommandLine } else { "" }
            $isManaged =
                ($executablePath -and ([StringComparer]::OrdinalIgnoreCase.Equals(
                    [IO.Path]::GetFullPath($executablePath),
                    [IO.Path]::GetFullPath($expectedPython)))) -and
                ($commandLine -and $commandLine.IndexOf($BackendDir, [StringComparison]::OrdinalIgnoreCase) -ge 0)
            if (-not $isManaged) {
                Write-Warning "$Name PID $processId 已失效或不属于当前部署，跳过停止并清理 PID 文件。"
                Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
                return
            }
            Write-Step "停止已有 $Name (PID $processId)"
            & taskkill.exe /PID $processId /T /F | Out-Null
            if ($LASTEXITCODE -ne 0) {
                throw "无法停止 $Name (PID $processId)。请使用管理员身份运行 PowerShell，再执行 -StopOnly 或 -Restart。"
            }
        }
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Assert-ProcessSlot([string]$PidFile, [string]$Name) {
    if (-not (Test-Path -LiteralPath $PidFile)) {
        return
    }
    $rawPid = (Get-Content -LiteralPath $PidFile -Raw).Trim()
    $processId = 0
    if ([int]::TryParse($rawPid, [ref]$processId)) {
        $process = Get-Process -Id $processId -ErrorAction SilentlyContinue
        if ($null -ne $process) {
            throw "$Name 已在运行 (PID $processId)。如需重启请追加 -Restart。"
        }
    }
    Remove-Item -LiteralPath $PidFile -Force -ErrorAction SilentlyContinue
}

function Invoke-Python([string[]]$Arguments) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        # PowerShell 5.1 会把原生程序 stderr 转成 NativeCommandError；
        # 暂时放宽错误偏好，才能保留 Python 的完整错误输出并自行检查退出码。
        $ErrorActionPreference = "Continue"
        & $script:PythonExe @Arguments 2>&1
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        throw "命令失败: $($script:PythonExe) $($Arguments -join ' ')"
    }
}

function Get-PythonValue([string]$Code) {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $script:PythonExe -c $Code 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        $details = ($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
        throw "无法读取应用配置：$details"
    }
    $value = ($output | Select-Object -Last 1).ToString().Trim()
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "应用配置返回空值"
    }
    return $value
}

function Ensure-Pip {
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        & $script:PythonExe -m pip --version *> $null
        $pipExitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($pipExitCode -eq 0) {
        return
    }

    Write-Step "虚拟环境中未检测到 pip，正在初始化"
    $previousErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = "Continue"
        $output = @(& $script:PythonExe -m ensurepip --upgrade 2>&1)
        $exitCode = $LASTEXITCODE
    } finally {
        $ErrorActionPreference = $previousErrorActionPreference
    }
    if ($exitCode -ne 0) {
        $details = ($output | ForEach-Object { $_.ToString() }) -join [Environment]::NewLine
        throw "虚拟环境中没有 pip，且 ensurepip 初始化失败：$details。请安装包含 pip/ensurepip 的 Python。"
    }
}

Set-Location -LiteralPath $BackendDir

if ($StopOnly) {
    Stop-ManagedProcess $BackendPidFile "FastAPI"
    Stop-ManagedProcess $WorkerPidFile "Worker"
    Write-Host "[deploy] 已停止脚本管理的服务。" -ForegroundColor Green
    exit 0
}

New-Item -ItemType Directory -Force -Path $DataDir, $RuntimeDir, $LogDir | Out-Null
$pipCacheDir = Join-Path $DataDir "pip-cache"
New-Item -ItemType Directory -Force -Path $pipCacheDir | Out-Null
$env:PIP_CACHE_DIR = $pipCacheDir

$pythonCommand = Get-Command python -CommandType Application -ErrorAction SilentlyContinue |
    Select-Object -First 1
if ($null -eq $pythonCommand) {
    throw "未找到 Python。请先安装 Python 3.11 或更高版本，并确保 python 已加入 PATH。"
}
$basePython = $pythonCommand.Path
if ([string]::IsNullOrWhiteSpace($basePython)) {
    $basePython = $pythonCommand.Source
}
if ([string]::IsNullOrWhiteSpace($basePython) -or -not (Test-Path -LiteralPath $basePython)) {
    throw "无法解析 Python 可执行文件路径，请确认 python 已加入 PATH。"
}
$venvDir = Join-Path $BackendDir ".venv"
$venvPython = Join-Path $venvDir "Scripts\python.exe"
if (-not (Test-Path -LiteralPath $venvPython)) {
    Write-Step "创建 Python 虚拟环境"
    & $basePython -m venv $venvDir
    if ($LASTEXITCODE -ne 0) {
        throw "创建 Python 虚拟环境失败"
    }
}
$script:PythonExe = $venvPython
Ensure-Pip

if (-not (Test-Path -LiteralPath ".env")) {
    if ($Environment -eq "production") {
        throw "生产部署要求先创建 backend/.env；请参考 .env.example 填入数据库地址和随机强密钥。"
    }
    if (-not (Test-Path -LiteralPath ".env.example")) {
        throw "缺少 .env 和 .env.example"
    }
    Copy-Item -LiteralPath ".env.example" -Destination ".env"
    Write-Warning "已从 .env.example 创建开发配置，请勿将其用于生产。"
}

# 环境变量覆盖 .env，确保本次部署的安全基线与启动进程一致。
$env:ENVIRONMENT = $Environment

if (-not $SkipInstall) {
    if (-not (Test-Path -LiteralPath "requirements.txt")) {
        throw "缺少 requirements.txt，无法安装依赖"
    }
    Write-Step "使用 pip 安装依赖"
    Invoke-Python @("-m", "pip", "install", "--requirement", "requirements.txt")
}

if ([string]::IsNullOrWhiteSpace($WorkerId)) {
    $WorkerId = Get-PythonValue 'from app.core.config import settings; print(settings.worker_id)'
}

if ($Restart) {
    Stop-ManagedProcess $BackendPidFile "FastAPI"
    Stop-ManagedProcess $WorkerPidFile "Worker"
}
Assert-ProcessSlot $BackendPidFile "FastAPI"
Assert-ProcessSlot $WorkerPidFile "Worker"

Write-Step "检查生产安全配置"
Invoke-Python @("-c", "from app.core.config import validate_security_baseline; validate_security_baseline()")

$reportsPath = Get-PythonValue 'from app.core.config import reports_dir; print(reports_dir())'
$releasesPath = Get-PythonValue 'from app.core.config import releases_dir; print(releases_dir())'
New-Item -ItemType Directory -Force -Path $reportsPath, $releasesPath | Out-Null

if (-not $SkipMigrate) {
    Write-Step "执行数据库迁移"
    Invoke-Python @("-m", "alembic", "upgrade", "head")
}

Write-Step "检查数据库迁移漂移"
Invoke-Python @("-m", "alembic", "check")

if ($Seed) {
    Write-Step "初始化 admin/admin123（首次登录后必须改密）"
    Invoke-Python @("-m", "app.seed")
}

if ($NoStart) {
    Write-Host "[deploy] 准备完成，因指定 -NoStart 未启动服务。" -ForegroundColor Green
    exit 0
}

$wsMaxSize = [int](Get-PythonValue 'from app.core.config import settings; print(settings.agent_ws_max_frame_bytes)')
$workerMode = Get-PythonValue 'from app.core.config import settings; print(settings.worker_mode)'
if ($workerMode -eq "external") {
    if ($Environment -eq "production" -and $WorkerId -eq "") {
        throw "external Worker 必须有非空 WorkerId"
    }
    $workerId = $WorkerId
} else {
    $workerId = ""
}

Write-Step "启动 FastAPI（单进程，Worker mode=$workerMode）"
$backendArguments = @(
    "-m", "uvicorn", "app.main:app",
    "--host", $ListenHost,
    "--port", $Port.ToString(),
    "--ws-max-size", $wsMaxSize.ToString()
)
$backendProcess = Start-Process `
    -FilePath $script:PythonExe `
    -ArgumentList $backendArguments `
    -WorkingDirectory $BackendDir `
    -RedirectStandardOutput $BackendStdout `
    -RedirectStandardError $BackendStderr `
    -WindowStyle Hidden `
    -PassThru
$backendProcess.Id | Set-Content -LiteralPath $BackendPidFile -Encoding ascii

if ($workerMode -eq "external") {
    Write-Step "启动独立 Worker ($workerId)"
    $workerArguments = @("worker.py", "--worker-id", $workerId, "--enable-scans")
    $workerProcess = Start-Process `
        -FilePath $script:PythonExe `
        -ArgumentList $workerArguments `
        -WorkingDirectory $BackendDir `
        -RedirectStandardOutput $WorkerStdout `
        -RedirectStandardError $WorkerStderr `
        -WindowStyle Hidden `
        -PassThru
    $workerProcess.Id | Set-Content -LiteralPath $WorkerPidFile -Encoding ascii
}

$healthUrl = "http://127.0.0.1:$Port/api/health"
$deadline = (Get-Date).AddSeconds($HealthTimeoutSeconds)
$healthy = $false
while ((Get-Date) -lt $deadline) {
    try {
        $response = Invoke-WebRequest -Uri $healthUrl -UseBasicParsing -TimeoutSec 5
        if ($response.StatusCode -eq 200 -and $response.Content -match '"status"\s*:\s*"ok"') {
            $healthy = $true
            break
        }
    } catch {
        Start-Sleep -Seconds 1
    }
}

if (-not $healthy) {
    Write-Warning "健康检查失败，请查看：$BackendStderr"
    if ($workerMode -eq "external") {
        Write-Warning "Worker 日志：$WorkerStderr"
    }
    exit 1
}

Write-Host "[deploy] 部署成功" -ForegroundColor Green
Write-Host "[deploy] API: $healthUrl"
Write-Host "[deploy] FastAPI PID: $($backendProcess.Id)"
if ($workerMode -eq "external") {
    Write-Host "[deploy] Worker PID: $($workerProcess.Id)"
}
Write-Host "[deploy] 日志: $LogDir"

