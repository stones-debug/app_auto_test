import logging
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager
from typing import Any

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.openapi.utils import get_openapi
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from starlette.datastructures import Headers

from app.api.agent import router as agent_router
from app.api.agents import router as agents_router
from app.api.app_profiles import router as app_profiles_router
from app.api.auth import router as auth_router
from app.api.cases import router as cases_router
from app.api.dashboard import router as dashboard_router
from app.api.deps import require_internal_token
from app.api.elements import router as elements_router
from app.api.executions import router as executions_router
from app.api.internal import router as internal_router
from app.api.me import router as me_router
from app.api.projects import router as projects_router
from app.api.releases import router as releases_router
from app.api.reports import router as reports_router
from app.api.suites import router as suites_router
from app.api.variables import router as variables_router
from app.core.config import BASE_DIR, settings, validate_security_baseline
from app.core.errors import ApiErrorDetail, ApiErrorResponse
from app.core.request_logging import (
    MAX_BODY_LOG_BYTES,
    format_for_log,
    parse_body_for_log,
    sanitize_request_body_for_log,
)
from app.core.security import is_loopback_host
from app.services.worker_runtime import WorkerRuntime
from app.ws.managers import agent_manager
from app.ws.routes import router as ws_router

# 使用 Uvicorn 的应用日志通道，确保默认启动命令也能看到 Worker 模式。
logger = logging.getLogger("uvicorn.error")


@asynccontextmanager
async def lifespan(_app: FastAPI):
    # CR-21：生产环境启动前校验安全基线（默认密钥/弱配置直接拒绝启动）
    validate_security_baseline()
    runtime: WorkerRuntime | None = None
    if settings.worker_mode == "embedded":
        runtime = WorkerRuntime(
            settings.worker_id,
            enable_scans=True,
            agent_sender=agent_manager.send,
        )
        await runtime.start()
        logger.info(
            "FastAPI 已启动嵌入式 Worker（id=%s, scans=true）",
            settings.worker_id,
        )
    elif settings.worker_mode == "external":
        logger.info("Worker 模式为 external，请单独启动 worker.py")
    else:
        logger.warning("Worker 模式为 disabled：执行只会入队，不会被消费")
    _app.state.worker_runtime = runtime
    try:
        yield
    finally:
        if runtime is not None:
            await runtime.stop()
        _app.state.worker_runtime = None


app = FastAPI(
    title="APP 自动化测试平台",
    version="0.1.0",
    docs_url="/api/docs",
    openapi_url="/api/openapi.json",
    lifespan=lifespan,
)

class RequestLoggingMiddleware:
    """记录 HTTP 请求参数，同时限制日志预读请求体的大小。"""

    def __init__(self, app: Callable) -> None:
        self.app = app

    async def __call__(
        self,
        scope: dict[str, Any],
        receive: Callable[[], Awaitable[dict[str, Any]]],
        send: Callable[[dict[str, Any]], Awaitable[None]],
    ) -> None:
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return

        headers = Headers(scope=scope)
        content_type = headers.get("content-type", "")
        content_length = _parse_content_length(headers.get("content-length"))
        request_logger = logging.getLogger("app.request")

        # 文件上传和超过日志上限的请求不能为了记录日志而预读正文。
        skip_reason: str | None = None
        replay_receive: Callable[[], Awaitable[dict[str, Any]]]
        if "multipart/form-data" in content_type.lower():
            skip_reason = "multipart"
        elif content_length is not None and content_length > MAX_BODY_LOG_BYTES:
            skip_reason = "too_large"

        if skip_reason is not None:
            body_log: Any = {"skipped": skip_reason}
            if content_type:
                body_log["content_type"] = content_type
            if content_length is not None:
                body_log["size"] = content_length
            replay_receive = receive
        else:
            # 只预读日志上限 + 1 字节，用于判断是否截断；原始 ASGI 消息保留并
            # 按原顺序回放，避免复制或消费完整请求体。
            buffered_messages: list[dict[str, Any]] = []
            preview = bytearray()
            body_complete = False
            while not body_complete and len(preview) <= MAX_BODY_LOG_BYTES:
                message = await receive()
                if message.get("type") == "http.disconnect":
                    return
                buffered_messages.append(message)
                body = message.get("body", b"")
                if body and len(preview) <= MAX_BODY_LOG_BYTES:
                    remaining = MAX_BODY_LOG_BYTES + 1 - len(preview)
                    preview.extend(body[:remaining])
                body_complete = not message.get("more_body", False)
                if len(preview) > MAX_BODY_LOG_BYTES:
                    break

            body_truncated = len(preview) > MAX_BODY_LOG_BYTES or not body_complete
            parsed_preview = parse_body_for_log(
                bytes(preview[:MAX_BODY_LOG_BYTES]), content_type
            )
            if body_truncated:
                body_log = {
                    "preview": parsed_preview,
                    "truncated": True,
                    "size": content_length,
                }
            else:
                body_log = parsed_preview

            buffered_index = 0

            async def _replay_receive() -> dict[str, Any]:
                nonlocal buffered_index
                if buffered_index < len(buffered_messages):
                    message = buffered_messages[buffered_index]
                    buffered_index += 1
                    return message
                # StreamingResponse/FileResponse 会并行监听客户端断开。这里回放完
                # 已读取消息后继续等待原始连接，不能立即伪造 disconnect。
                return await receive()

            replay_receive = _replay_receive

        request = Request(scope, receive=replay_receive)
        started = time.perf_counter()
        body_log = sanitize_request_body_for_log(request.url.path, body_log)
        request_logger.info(
            "HTTP 请求 %s %s query=%s body=%s",
            request.method,
            request.url.path,
            format_for_log(dict(request.query_params)),
            format_for_log(body_log),
        )

        response_status = 500

        async def log_response(message: dict[str, Any]) -> None:
            nonlocal response_status
            if message.get("type") == "http.response.start":
                response_status = int(message.get("status", 500))
            await send(message)

        try:
            await self.app(scope, replay_receive, log_response)
        except Exception:
            request_logger.exception(
                "HTTP 请求异常 %s %s duration_ms=%.1f",
                request.method,
                request.url.path,
                (time.perf_counter() - started) * 1000,
            )
            raise
        request_logger.info(
            "HTTP 响应 %s %s status=%s duration_ms=%.1f",
            request.method,
            request.url.path,
            response_status,
            (time.perf_counter() - started) * 1000,
        )


def _parse_content_length(value: str | None) -> int | None:
    if value is None:
        return None
    try:
        length = int(value)
    except (TypeError, ValueError):
        return None
    return length if length >= 0 else None


app.add_middleware(
    CORSMiddleware,
    allow_origins=settings.cors_origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)
app.add_middleware(RequestLoggingMiddleware)

app.include_router(auth_router, prefix="/api")
app.include_router(projects_router, prefix="/api")
app.include_router(app_profiles_router, prefix="/api")
app.include_router(elements_router, prefix="/api")
app.include_router(cases_router, prefix="/api")
app.include_router(suites_router, prefix="/api")
app.include_router(variables_router, prefix="/api")
app.include_router(executions_router, prefix="/api")
app.include_router(dashboard_router, prefix="/api")
app.include_router(agent_router, prefix="/api")
app.include_router(agents_router, prefix="/api")
app.include_router(me_router, prefix="/api")
app.include_router(releases_router, prefix="/api")
app.include_router(reports_router, prefix="/api")
app.include_router(internal_router)
app.include_router(ws_router)


def _custom_openapi():
    if app.openapi_schema:
        return app.openapi_schema
    schema = get_openapi(
        title=app.title,
        version=app.version,
        description=app.description,
        routes=app.routes,
    )
    components = schema.setdefault("components", {}).setdefault("schemas", {})
    components["ApiErrorDetail"] = ApiErrorDetail.model_json_schema()
    components["ApiErrorResponse"] = {
        "type": "object",
        "properties": {
            "detail": {"$ref": "#/components/schemas/ApiErrorDetail"},
        },
        "required": ["detail"],
        "title": ApiErrorResponse.__name__,
    }
    error_response = {
        "description": "统一业务错误",
        "content": {
            "application/json": {
                "schema": {"$ref": "#/components/schemas/ApiErrorResponse"},
            }
        },
    }
    for path_item in schema.get("paths", {}).values():
        for operation in path_item.values():
            if not isinstance(operation, dict) or "responses" not in operation:
                continue
            for status_code in ("400", "401", "403", "404", "409"):
                operation["responses"].setdefault(status_code, error_response)
    app.openapi_schema = schema
    return schema


app.openapi = _custom_openapi


@app.get("/metrics")
async def metrics(
    request: Request,
    _token: str = Depends(require_internal_token),
):
    """方案 §10.5：轻量指标暴露（需内部令牌，可选限制仅本机访问）。

    Step 5：原先该端点完全无鉴权，保护依据是"部署在内网"这一未被强制的假设，
    会泄漏执行量、失败率、Agent 数量等运营数据。现与 /internal/* 共用内部令牌校验；
    metrics_require_loopback=true 时再叠加来源地址限制（仅本机 Prometheus 场景）；
    远程 Prometheus 应使用网络白名单或反向代理鉴权。
    """
    from fastapi.responses import PlainTextResponse

    from app.services.metrics import render_metrics

    if settings.metrics_require_loopback:
        host = request.client.host if request.client else None
        if not is_loopback_host(host):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN, detail="指标端点仅允许本机访问"
            )
    return PlainTextResponse(render_metrics(), media_type="text/plain; version=0.0.4")


@app.get("/api/health")
async def health() -> dict:
    return {"status": "ok", "version": "0.1.0"}


# ---------- 前端静态托管（本地部署：单端口 8001 提供页面 + API + WS） ----------
_FRONTEND_DIST = BASE_DIR.parent / "frontend" / "dist"

if _FRONTEND_DIST.exists():
    _assets_dir = _FRONTEND_DIST / "assets"
    if _assets_dir.exists():
        app.mount("/assets", StaticFiles(directory=_assets_dir), name="assets")

    _favicon = _FRONTEND_DIST / "favicon.svg"
    if _favicon.exists():

        @app.get("/favicon.svg", include_in_schema=False)
        async def favicon():
            return FileResponse(_favicon, media_type="image/svg+xml")

    @app.api_route(
        "/{full_path:path}",
        methods=["GET", "POST", "PUT", "PATCH", "DELETE", "OPTIONS", "HEAD"],
        include_in_schema=False,
    )
    async def spa_fallback(full_path: str):
        # /api、/ws 由路由处理，未匹配的返回 404 而非回退 index.html
        if full_path.startswith(("api", "ws")):
            raise HTTPException(status_code=404)
        index = _FRONTEND_DIST / "index.html"
        if not index.exists():
            raise HTTPException(status_code=404)
        return FileResponse(index)


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8001, log_level="info", reload=True)
