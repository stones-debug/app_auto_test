from pathlib import Path
from typing import Literal

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


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
    max_execution_timeout: int = 7200
    # CR-06：停止宽限期（stopping 超过该期限强制终态并释放设备）
    execution_stop_grace_seconds: int = 60
    # 方案 §3.6/§7.4：执行快照序列化上限（默认 20 MB）
    max_execution_snapshot_bytes: int = 20971520
    # 多 APP 档案灰度：off=兼容旧流程；compat=仅指定项目自动注入默认档案；required=全量显式必选。
    app_profile_feature_mode: Literal["off", "compat", "required"] = "off"
    app_profile_enabled_project_ids: str = ""

    # Agent
    agent_heartbeat_interval: int = 30
    agent_heartbeat_timeout: int = 120
    min_agent_version: str = "1.0.0"
    internal_token: str = "dev-internal-token-change-me"
    backend_base_url: str = "http://127.0.0.1:8001"
    # Windows 方案 §3.2：用户 Agent Key 可逆加密主密钥（生产必填）
    agent_user_key_encryption_key: str = ""

    # Worker
    worker_mode: Literal["embedded", "external", "disabled"] = "embedded"
    worker_id: str = "worker-001"
    worker_poll_interval: int = 2
    worker_claim_stale_minutes: int = 10

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


def validate_security_baseline() -> None:
    """CR-21：非 development 环境遇到默认密钥/弱配置时拒绝启动。"""
    if settings.environment == "development":
        return
    problems: list[str] = []
    if settings.jwt_secret_key in _DEFAULT_SECRETS or len(settings.jwt_secret_key) < 32:
        problems.append("jwt_secret_key 必须为随机长密钥（>=32 字符），不能使用默认值")
    if settings.internal_token in _DEFAULT_SECRETS:
        problems.append("internal_token 不能使用默认值")
    if len(settings.agent_user_key_encryption_key) < 32:
        problems.append("agent_user_key_encryption_key 必须为随机长密钥（>=32 字符）")
    if "dev123" in settings.database_url:
        problems.append("数据库密码不能使用默认值 dev123")
    if problems:
        raise RuntimeError("部署安全基线未通过: " + "; ".join(problems))
