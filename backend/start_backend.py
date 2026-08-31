"""直接使用当前 Python 环境启动 FastAPI 后端。

用法（在 backend 目录执行）：
    python start_backend.py
    python start_backend.py --host 0.0.0.0 --port 8001
    python start_backend.py --migrate --seed
    python start_backend.py --reload

脚本不依赖 uv，适用于系统 Python 或 backend/.venv 中的 Python。
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path


def _run_module(module: str, *module_args: str) -> None:
    command = [sys.executable, "-m", module, *module_args]
    print(f"[run] {' '.join(command)}", flush=True)
    subprocess.run(command, check=True)


def main() -> None:
    parser = argparse.ArgumentParser(description="启动 APP 自动化测试平台 FastAPI 后端")
    parser.add_argument("--host", default="0.0.0.0", help="监听地址，默认 0.0.0.0")
    parser.add_argument("--port", type=int, default=8001, help="监听端口，默认 8001")
    parser.add_argument(
        "--ws-max-size",
        type=int,
        default=None,
        help="WebSocket 最大帧大小（字节），默认读取配置",
    )
    parser.add_argument("--reload", action="store_true", help="开发模式启用代码热重载")
    parser.add_argument("--migrate", action="store_true", help="启动前执行数据库迁移")
    parser.add_argument("--seed", action="store_true", help="启动前初始化默认 admin 账号")
    args = parser.parse_args()

    backend_dir = Path(__file__).resolve().parent
    os.chdir(backend_dir)

    if args.migrate:
        _run_module("alembic", "upgrade", "head")
    if args.seed:
        _run_module("app.seed")

    from app.core.config import settings

    ws_max_size = args.ws_max_size or settings.agent_ws_max_frame_bytes
    reload_dirs = [str(backend_dir)] if args.reload else None
    mode = "reload" if args.reload else "no-reload"
    print(
        f"[start] FastAPI + Worker mode={settings.worker_mode} ({mode})",
        flush=True,
    )
    print(
        f"[start] http://{args.host}:{args.port}  WebSocket max size={ws_max_size}",
        flush=True,
    )

    import uvicorn

    uvicorn.run(
        "app.main:app",
        host=args.host,
        port=args.port,
        ws_max_size=ws_max_size,
        reload=args.reload,
        reload_dirs=reload_dirs,
    )


if __name__ == "__main__":
    main()
