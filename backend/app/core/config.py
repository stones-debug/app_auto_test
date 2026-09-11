import logging
from pathlib import Path
from typing import Literal

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def _warn(message: str, *args: object) -> None:
    """输出启动告警。

    uvicorn 的 dictConfig 默认 `disable_existing_loggers=True`，会把导入早于它的
    logger 置为 disabled；本模块位于导入链最早期，正是受害者之一
    （实测：app.main / uvicorn.error 均为 disabled=False，仅 app.core.config 为 True）。
    不处理的话下面的弱配置告警会被静默丢弃——那样这个告警就白加了。
    因此每次输出前先恢复启用状态，保证弱配置一定可见。
    """
    log = logging.getLogger(__name__)
    if log.disabled:
        log.disabled = False
    log.warning(message, *args)


def reports_dir() -> Path:
    base = Path(settings.reports_base_path)
    return base if base.is_absolute() else BASE_DIR / base


def releases_dir() -> Path:
    """Windows 方案 §3.4：Agent 安装包发布目录。"""
    base = Path(settings.agent_releases_path)
    return base if base.is_absolute() else BASE_DIR / base


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=BASE_DIR / ".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # CR-21：部署环境（production 拒绝默认密钥/弱配置）
    environment: str = "development"  # development / production

    # 数据库
    database_url: str = "postgresql+asyncpg://dev:dev123@127.0.0.1:5432/test_platform"

    # JWT
    jwt_secret_key: str = "dev-secret-key-change-me-in-production-at-least-32-chars"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 120
    jwt_refresh_token_expire_days: int = 7

    # 执行
    default_execution_timeout: int = 1800
    max_execution_timeout: int = Field(default=86400, ge=60, le=86400)
    # CR-06：停止宽限期（stopping 超过该期限强制终态并释放设备）
    execution_stop_grace_seconds: int = 60
    # stop_test 幂等重试退避；stop_command_sent_at 记录最近一次尝试
    execution_stop_retry_seconds: int = Field(default=5, ge=1, le=60)
    # 方案 §3.6/§7.4：执行快照序列化上限（默认 20 MB）
    max_execution_snapshot_bytes: int = 20971520
    # 预检快照仅用于紧邻的创建请求，防止服务端重复解析。
    execution_prepare_ttl_seconds: int = Field(default=60, ge=1, le=600)
    # 多 APP 档案灰度：off=兼容旧流程；compat=仅指定项目自动注入默认档案；required=全量显式必选。
    app_profile_feature_mode: Literal["off", "compat", "required"] = "off"
    app_profile_enabled_project_ids: str = ""

    # Agent
    agent_heartbeat_interval: int = 30
    agent_heartbeat_timeout: int = 120
    # Agent 准入版本以 Registry 产物为运行时事实来源；该配置默认值保持
    # 与当前发布包一致，避免脱离数据库/协议文件启动时仍宣称支持旧包。
    min_agent_version: str = "3.3.0"
    internal_token: str = "dev-internal-token-change-me"
    backend_base_url: str = "http://127.0.0.1:8001"
    # Windows 方案 §3.2：用户 Agent Key 可逆加密主密钥（生产必填）
    agent_user_key_encryption_key: str = ""
    # 当前用户变量独立 Fernet 主密钥（生产必填）
    user_variable_encryption_key: str = ""

    # Worker
    worker_mode: Literal["embedded", "external", "disabled"] = "embedded"
    worker_id: str = "worker-001"
    worker_poll_interval: int = 2
    worker_claim_stale_minutes: int = 10
    worker_concurrency: int = Field(default=1, ge=1, le=32)
    worker_shutdown_grace_seconds: int = Field(default=30, ge=0, le=600)
    worker_agent_send_timeout_seconds: int = Field(default=10, ge=1, le=60)

    # 分页
    max_page_size: int = 200

    # CR-21：接口限流（每分钟每 IP）
    rate_limit_auth_per_minute: int = 60
    rate_limit_upload_per_minute: int = 120
    rate_limit_execution_per_minute: int = 60
    # Windows 方案 §3.2：Agent 绑定/解绑接口限流
    rate_limit_bind_per_minute: int = 20
    # 方案 §10.3：预检按 user+project 限流
    rate_limit_preview_per_minute: int = 30

    # 存储
    reports_base_path: str = "./data/reports"
    max_upload_size: int = 524288000  # 500MB
    max_screenshot_size: int = 10485760  # 10MB
    # Windows 方案 §3.4：Agent 安装包发布目录（latest.json + 版本化安装包）
    agent_releases_path: str = "./data/agent-releases"

    # 清理
    report_retention_days: int = 90
    screenshot_retention_days: int = 30
    log_retention_days: int = 7

    # Step 8：报告详情日志上限（超限只返回最后 N 条）
    report_max_logs: int = 20000

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]

    # Step 5：/metrics 端点来源限制（纵深防御；主防护为 internal token）
    # true 时仅接受 loopback 来源，适用于 Prometheus 部署在本机的场景
    metrics_require_loopback: bool = False

    # Step 5：Agent WS 连接级防护——未注册连接不得长期占用网关资源
    # 阈值刻意设宽松（弱网 Agent 可能重连频繁），上线观察后再收紧
    agent_ws_max_frame_bytes: int = Field(default=1048576, gt=0)
    agent_ws_max_pre_register_messages: int = Field(default=20, gt=0)
    agent_ws_register_timeout_seconds: int = Field(default=30, gt=0)


settings = Settings()


def app_profile_enabled_project_ids() -> set[int]:
    """解析逗号分隔的灰度项目 ID；非法项在启动/请求前明确拒绝。"""
    result: set[int] = set()
    for raw in settings.app_profile_enabled_project_ids.split(","):
        value = raw.strip()
        if not value:
            continue
        if not value.isdigit() or int(value) < 1:
            raise RuntimeError(f"APP_PROFILE_ENABLED_PROJECT_IDS 包含非法项目 ID: {value}")
        result.add(int(value))
    return result


def app_profile_required_for_project(project_id: int) -> bool:
    if settings.app_profile_feature_mode == "required":
        return True
    return (
        settings.app_profile_feature_mode == "compat"
        and project_id in app_profile_enabled_project_ids()
    )

# 默认密钥（生产环境必须覆盖）
_DEFAULT_SECRETS = (
    "dev-secret-key-change-me-in-production-at-least-32-chars",
    "dev-internal-token-change-me",
)


def _is_weak_internal_token(value: str) -> bool:
    """生产环境内部令牌必须是非默认且至少 32 个字符的值。"""
    return value in _DEFAULT_SECRETS or len(value) < 32


def weak_secret_names() -> list[str]:
    """返回仍在使用默认值/弱值的配置项名称（development 告警与自检共用）。"""
    weak: list[str] = []
    if settings.jwt_secret_key in _DEFAULT_SECRETS or len(settings.jwt_secret_key) < 32:
        weak.append("jwt_secret_key")
    if _is_weak_internal_token(settings.internal_token):
        weak.append("internal_token")
    if len(settings.agent_user_key_encryption_key) < 32:
        weak.append("agent_user_key_encryption_key")
    if len(settings.user_variable_encryption_key) < 32:
        weak.append("user_variable_encryption_key")
    if "dev123" in settings.database_url:
        weak.append("database_url(默认密码 dev123)")
    return weak


def _warn_development_weaknesses() -> None:
    """Step 5：development 下不阻断启动，但把弱配置显式暴露出来。

    validate_security_baseline 在 development 下直接 return，历史上导致弱配置零可见性：
    "能正常启动"被误读成"配置是安全的"。这里补一条启动告警，只增加可见性，不改变行为。
    """
    weak = weak_secret_names()
    if weak:
        _warn(
            "当前为 development 环境，以下配置仍是默认值/弱值，仅允许本地开发使用，"
            "部署前必须覆盖：%s",
            "、".join(weak),
        )


def _warn_production_advisories() -> None:
    """Step 5：生产环境的可选加固建议——只告警，不阻断启动。"""
    if not settings.metrics_require_loopback:
        _warn(
            "/metrics 已要求内部令牌，但未限制来源地址；若 Prometheus 不在本机，"
            "请使用网络白名单或反向代理鉴权限制该端点的访问范围；"
            "metrics_require_loopback=true 仅适用于本机采集器，开启后远程采集会被拒绝"
        )


def validate_security_baseline() -> None:
    """CR-21：非 development 环境遇到默认密钥/弱配置时拒绝启动。"""
    if settings.environment == "development":
        _warn_development_weaknesses()
        return
    problems: list[str] = []
    if settings.jwt_secret_key in _DEFAULT_SECRETS or len(settings.jwt_secret_key) < 32:
        problems.append("jwt_secret_key 必须为随机长密钥（>=32 字符），不能使用默认值")
    if _is_weak_internal_token(settings.internal_token):
        problems.append("internal_token 必须为非默认随机长令牌（>=32 字符）")
    if len(settings.agent_user_key_encryption_key) < 32:
        problems.append("agent_user_key_encryption_key 必须为随机长密钥（>=32 字符）")
    if len(settings.user_variable_encryption_key) < 32:
        problems.append("user_variable_encryption_key 必须为随机长密钥（>=32 字符）")
    if "dev123" in settings.database_url:
        problems.append("数据库密码不能使用默认值 dev123")
    # Step 5：凭据模式下通配源等于对任意站点开放（allow_credentials=True）
    if "*" in settings.cors_origins:
        problems.append("cors_origins 不能包含通配符 *（与 allow_credentials=True 冲突）")
    if problems:
        raise RuntimeError("部署安全基线未通过: " + "; ".join(problems))
    _warn_production_advisories()
