<#
.SYNOPSIS
启动后端 FastAPI 服务（APP 自动化测试平台）。

.DESCRIPTION
- 自动将 uv 加入 PATH（uv 不在默认 PATH）
- 若缺少 .env，从 .env.example 复制
- 可选执行数据库迁移（-Migrate）与种子数据（-Seed）
- 前台启动 uvicorn，Ctrl+C 停止；生产环境加 -NoReload
- 默认端口 8001（本机 8000 被 C-Lodop 打印服务占用）

.EXAMPLE
.\start-backend.ps1
.\start-backend.ps1 -Migrate -Seed
.\start-backend.ps1 -Port 8001 -NoReload
#>

param(
    [int]$Port = 8001,
    [string]$ListenHost = "0.0.0.0",
    [switch]$Migrate,
    [switch]$Seed,
    [switch]$NoReload
)

$ErrorActionPreference = "Stop"
$BackendDir = Split-Path -Parent $MyInvocation.MyCommand.Path

# uv 不在默认 PATH，需手动加入
$uvPath = Join-Path $env:USERPROFILE ".local\bin"
if (Test-Path (Join-Path $uvPath "uv.exe")) {
    $env:Path = "$uvPath;" + $env:Path
} else {
    Write-Warning "未找到 uv（$uvPath），请先安装：irm https://astral.sh/uv/install.ps1 | iex"
}

Set-Location $BackendDir

# 确保 .env 存在
if (-not (Test-Path ".env")) {
    if (Test-Path ".env.example") {
        Copy-Item ".env.example" ".env"
        Write-Host "[init] 已从 .env.example 生成 .env，请按需修改配置"
    } else {
        Write-Warning "[init] 缺少 .env 和 .env.example"
    }
}

# 可选：数据库迁移
if ($Migrate) {
    Write-Host "[migrate] 执行 alembic upgrade head ..."
    uv run alembic upgrade head
    if (-not $?) { exit 1 }
}

# 可选：种子数据
if ($Seed) {
    Write-Host "[seed] 创建 admin/admin123 用户 ..."
    uv run python -m app.seed
    if (-not $?) { exit 1 }
}

$mode = if ($NoReload) { "no-reload" } else { "reload" }
$wsMaxSize = [int](uv run python -c "from app.core.config import settings; print(settings.agent_ws_max_frame_bytes)")
if (-not $?) { exit 1 }
$workerMode = "embedded"
if (Test-Path ".env") {
    $workerModeLine = Get-Content ".env" |
        Where-Object { $_ -match '^\s*WORKER_MODE\s*=' } |
        Select-Object -First 1
    if ($workerModeLine) {
        $workerMode = ($workerModeLine -split '=', 2)[1].Trim().Trim('"').Trim("'")
    }
}
Write-Host "[start] FastAPI + Worker mode=$workerMode ($mode)"
Write-Host "[start] uvicorn app.main:app --host $ListenHost --port $Port --ws-max-size $wsMaxSize"

if ($NoReload) {
    uv run uvicorn app.main:app --host $ListenHost --port $Port --ws-max-size $wsMaxSize
} else {
    uv run uvicorn app.main:app --host $ListenHost --port $Port --ws-max-size $wsMaxSize --reload
}
