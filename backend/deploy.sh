#!/usr/bin/env bash
#
# APP 自动化测试平台后端 Linux 部署脚本
#
# 适用环境：Linux + PostgreSQL + uv。
# 脚本不会覆盖已有 .env，也不会自动生成生产密钥或修改数据库密码。
# 生产部署前请先根据 .env.example 准备 backend/.env。
#
# 示例：
#   ./deploy.sh                         # 迁移并启动
#   ./deploy.sh --seed                  # 同时初始化 admin/admin123
#   ./deploy.sh --no-start              # 只同步依赖、迁移和检查
#   ./deploy.sh --restart               # 重启脚本管理的进程
#   ./deploy.sh --stop                  # 停止脚本管理的进程
#   ./deploy.sh --environment development --skip-sync --skip-migrate
#

set -Eeuo pipefail
IFS=$'\n\t'
umask 027

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
BACKEND_DIR="$SCRIPT_DIR"
DATA_DIR="$BACKEND_DIR/data"
RUNTIME_DIR="$DATA_DIR/runtime"
LOG_DIR="$DATA_DIR/logs"
UV_CACHE_DIR="$DATA_DIR/uv-cache"
BACKEND_PID_FILE="$RUNTIME_DIR/backend.pid"
WORKER_PID_FILE="$RUNTIME_DIR/worker.pid"
BACKEND_STDOUT="$LOG_DIR/backend.out.log"
BACKEND_STDERR="$LOG_DIR/backend.err.log"
WORKER_STDOUT="$LOG_DIR/worker.out.log"
WORKER_STDERR="$LOG_DIR/worker.err.log"

ENVIRONMENT="production"
LISTEN_HOST="0.0.0.0"
PORT=8001
WORKER_ID=""
HEALTH_TIMEOUT=60
SEED=0
SKIP_SYNC=0
SKIP_MIGRATE=0
NO_START=0
RESTART=0
STOP_ONLY=0

usage() {
    sed -n '1,32p' "$0"
    cat <<'EOF'

参数：
  --environment <development|production>  部署环境，默认 production
  --host <address>                        监听地址，默认 0.0.0.0
  --port <number>                         监听端口，默认 8001
  --worker-id <id>                        external Worker ID，默认读取 .env
  --seed                                  初始化 admin/admin123
  --skip-sync                             跳过 uv sync --frozen --no-dev
  --skip-migrate                          跳过 alembic upgrade head
  --no-start                              只完成准备工作，不启动服务
  --restart                               停止并重启脚本管理的进程
  --stop                                  仅停止脚本管理的进程
  --health-timeout <seconds>              健康检查超时，默认 60
  -h, --help                              显示帮助
EOF
}

die() {
    echo "[deploy] ERROR: $*" >&2
    exit 1
}

step() {
    echo "[deploy] $*"
}

is_integer() {
    [[ "$1" =~ ^[0-9]+$ ]]
}

parse_args() {
    while (($# > 0)); do
        case "$1" in
            --environment)
                (($# >= 2)) || die "--environment 缺少参数"
                ENVIRONMENT="$2"
                shift 2
                ;;
            --host)
                (($# >= 2)) || die "--host 缺少参数"
                LISTEN_HOST="$2"
                shift 2
                ;;
            --port)
                (($# >= 2)) || die "--port 缺少参数"
                PORT="$2"
                shift 2
                ;;
            --worker-id)
                (($# >= 2)) || die "--worker-id 缺少参数"
                WORKER_ID="$2"
                shift 2
                ;;
            --health-timeout)
                (($# >= 2)) || die "--health-timeout 缺少参数"
                HEALTH_TIMEOUT="$2"
                shift 2
                ;;
            --seed) SEED=1; shift ;;
            --skip-sync) SKIP_SYNC=1; shift ;;
            --skip-migrate) SKIP_MIGRATE=1; shift ;;
            --no-start) NO_START=1; shift ;;
            --restart) RESTART=1; shift ;;
            --stop) STOP_ONLY=1; shift ;;
            -h|--help) usage; exit 0 ;;
            *) die "未知参数: $1（使用 --help 查看帮助）" ;;
        esac
    done

    [[ "$ENVIRONMENT" == "development" || "$ENVIRONMENT" == "production" ]] \
        || die "--environment 必须是 development 或 production"
    is_integer "$PORT" && ((PORT >= 1 && PORT <= 65535)) \
        || die "--port 必须是 1-65535 的整数"
    is_integer "$HEALTH_TIMEOUT" && ((HEALTH_TIMEOUT >= 1)) \
        || die "--health-timeout 必须是正整数"
}

find_uv() {
    if command -v uv >/dev/null 2>&1; then
        UV_BIN="$(command -v uv)"
    elif [[ -x "$HOME/.local/bin/uv" ]]; then
        UV_BIN="$HOME/.local/bin/uv"
    else
        die "未找到 uv，请先安装：https://docs.astral.sh/uv/getting-started/installation/"
    fi
}

run_uv() {
    "$UV_BIN" "$@"
}

uv_value() {
    local code="$1"
    local output
    output="$(run_uv run python -c "$code" 2>&1)" \
        || die "读取应用配置失败"
    printf '%s\n' "$output" | tail -n 1 | tr -d '\r'
}

pid_from_file() {
    local pid_file="$1"
    [[ -f "$pid_file" ]] || return 1
    local pid
    pid="$(tr -d '[:space:]' < "$pid_file")"
    is_integer "$pid" && ((pid > 0)) || return 1
    printf '%s\n' "$pid"
}

pid_is_running() {
    local pid="$1"
    kill -0 "$pid" 2>/dev/null
}

stop_managed_process() {
    local pid_file="$1"
    local name="$2"
    local pid
    pid="$(pid_from_file "$pid_file" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && pid_is_running "$pid"; then
        step "停止 $name (PID $pid)"
        kill -TERM -- "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
        for _ in {1..20}; do
            pid_is_running "$pid" || break
            sleep 1
        done
        if pid_is_running "$pid"; then
            kill -KILL -- "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
        fi
    fi
    rm -f -- "$pid_file"
}

assert_process_slot() {
    local pid_file="$1"
    local name="$2"
    local pid
    pid="$(pid_from_file "$pid_file" 2>/dev/null || true)"
    if [[ -n "$pid" ]] && pid_is_running "$pid"; then
        die "$name 已在运行 (PID $pid)，如需重启请追加 --restart"
    fi
    rm -f -- "$pid_file"
}

cleanup_after_start_failure() {
    stop_managed_process "$WORKER_PID_FILE" "Worker"
    stop_managed_process "$BACKEND_PID_FILE" "FastAPI"
}

wait_for_health() {
    local url="http://127.0.0.1:${PORT}/api/health"
    local deadline=$((SECONDS + HEALTH_TIMEOUT))
    step "等待健康检查: $url"
    while ((SECONDS < deadline)); do
        if curl --silent --show-error --fail --max-time 5 "$url" 2>/dev/null \
            | grep -q '"status"[[:space:]]*:[[:space:]]*"ok"'; then
            return 0
        fi
        sleep 1
    done
    return 1
}

start_backend() {
    local ws_max_size="$1"
    step "启动 FastAPI（单进程，WORKER_MODE=$WORKER_MODE）"
    if command -v setsid >/dev/null 2>&1; then
        setsid nohup "$UV_BIN" run uvicorn app.main:app \
            --host "$LISTEN_HOST" --port "$PORT" --ws-max-size "$ws_max_size" \
            >"$BACKEND_STDOUT" 2>"$BACKEND_STDERR" < /dev/null &
    else
        nohup "$UV_BIN" run uvicorn app.main:app \
            --host "$LISTEN_HOST" --port "$PORT" --ws-max-size "$ws_max_size" \
            >"$BACKEND_STDOUT" 2>"$BACKEND_STDERR" < /dev/null &
    fi
    BACKEND_PID=$!
    printf '%s\n' "$BACKEND_PID" > "$BACKEND_PID_FILE"
}

start_worker() {
    step "启动独立 Worker ($WORKER_ID)"
    if command -v setsid >/dev/null 2>&1; then
        setsid nohup "$UV_BIN" run python worker.py --worker-id "$WORKER_ID" --enable-scans \
            >"$WORKER_STDOUT" 2>"$WORKER_STDERR" < /dev/null &
    else
        nohup "$UV_BIN" run python worker.py --worker-id "$WORKER_ID" --enable-scans \
            >"$WORKER_STDOUT" 2>"$WORKER_STDERR" < /dev/null &
    fi
    WORKER_PID=$!
    printf '%s\n' "$WORKER_PID" > "$WORKER_PID_FILE"
}

main() {
    parse_args "$@"
    cd -- "$BACKEND_DIR"

    mkdir -p -- "$DATA_DIR" "$RUNTIME_DIR" "$LOG_DIR" "$UV_CACHE_DIR"
    export UV_CACHE_DIR

    if ((STOP_ONLY)); then
        stop_managed_process "$WORKER_PID_FILE" "Worker"
        stop_managed_process "$BACKEND_PID_FILE" "FastAPI"
        echo "[deploy] 已停止脚本管理的服务。"
        return 0
    fi

    if [[ ! -f .env ]]; then
        if [[ "$ENVIRONMENT" == "production" ]]; then
            die "生产部署要求先创建 backend/.env，请参考 .env.example 填入数据库地址和随机强密钥"
        fi
        [[ -f .env.example ]] || die "缺少 .env 和 .env.example"
        cp -- .env.example .env
        echo "[deploy] 已从 .env.example 创建开发配置，请勿用于生产"
    fi

    # 环境变量优先级高于 .env，保证检查和启动进程使用同一环境。
    export ENVIRONMENT
    if [[ -z "$WORKER_ID" ]]; then
        WORKER_ID="$(uv_value 'from app.core.config import settings; print(settings.worker_id)')"
    fi

    if ((RESTART)); then
        stop_managed_process "$WORKER_PID_FILE" "Worker"
        stop_managed_process "$BACKEND_PID_FILE" "FastAPI"
    fi
    assert_process_slot "$BACKEND_PID_FILE" "FastAPI"
    assert_process_slot "$WORKER_PID_FILE" "Worker"

    if ((SKIP_SYNC == 0)); then
        step "同步锁定的生产依赖"
        run_uv sync --frozen --no-dev
    fi

    step "检查安全配置"
    run_uv run python -c 'from app.core.config import validate_security_baseline; validate_security_baseline()'

    REPORTS_PATH="$(uv_value 'from app.core.config import reports_dir; print(reports_dir())')"
    RELEASES_PATH="$(uv_value 'from app.core.config import releases_dir; print(releases_dir())')"
    mkdir -p -- "$REPORTS_PATH" "$RELEASES_PATH"

    if ((SKIP_MIGRATE == 0)); then
        step "执行数据库迁移"
        run_uv run alembic upgrade head
    fi
    step "检查数据库迁移漂移"
    run_uv run alembic check

    if ((SEED)); then
        step "初始化 admin/admin123（首次登录后必须改密）"
        run_uv run python -m app.seed
    fi

    if ((NO_START)); then
        echo "[deploy] 准备完成，因指定 --no-start 未启动服务。"
        return 0
    fi

    WS_MAX_SIZE="$(uv_value 'from app.core.config import settings; print(settings.agent_ws_max_frame_bytes)')"
    WORKER_MODE="$(uv_value 'from app.core.config import settings; print(settings.worker_mode)')"
    [[ "$WORKER_MODE" == "embedded" || "$WORKER_MODE" == "external" || "$WORKER_MODE" == "disabled" ]] \
        || die "WORKER_MODE 配置无效: $WORKER_MODE"
    if [[ "$WORKER_MODE" == "external" && -z "$WORKER_ID" ]]; then
        die "WORKER_MODE=external 时 Worker ID 不能为空"
    fi

    start_backend "$WS_MAX_SIZE"
    if [[ "$WORKER_MODE" == "external" ]]; then
        start_worker
    fi

    if ! wait_for_health; then
        echo "[deploy] 健康检查失败，请查看 $BACKEND_STDERR" >&2
        [[ "$WORKER_MODE" == "external" ]] && echo "[deploy] Worker 日志: $WORKER_STDERR" >&2
        cleanup_after_start_failure
        return 1
    fi

    echo "[deploy] 部署成功"
    echo "[deploy] API: http://127.0.0.1:${PORT}/api/health"
    echo "[deploy] FastAPI PID: $BACKEND_PID"
    if [[ "$WORKER_MODE" == "external" ]]; then
        echo "[deploy] Worker PID: $WORKER_PID"
    fi
    echo "[deploy] 日志目录: $LOG_DIR"
}

main "$@"
