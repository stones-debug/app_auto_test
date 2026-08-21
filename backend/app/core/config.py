from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

BASE_DIR = Path(__file__).resolve().parent.parent.parent


def reports_dir() -> Path:
    base = Path(settings.reports_base_path)
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

    # Agent
    agent_heartbeat_interval: int = 30
    agent_heartbeat_timeout: int = 120
    min_agent_version: str = "1.0.0"
    internal_token: str = "dev-internal-token-change-me"
    backend_base_url: str = "http://127.0.0.1:8001"

    # Worker
    worker_poll_interval: int = 2
    worker_claim_stale_minutes: int = 10

    # 分页
    max_page_size: int = 200

    # CR-21：接口限流（每分钟每 IP）
    rate_limit_auth_per_minute: int = 60
    rate_limit_upload_per_minute: int = 120
    rate_limit_execution_per_minute: int = 60

    # 存储
    reports_base_path: str = "./data/reports"
    max_upload_size: int = 524288000  # 500MB
    max_screenshot_size: int = 10485760  # 10MB

    # 清理
    report_retention_days: int = 90
    screenshot_retention_days: int = 30
    log_retention_days: int = 7

    # CORS
    cors_origins: list[str] = ["http://localhost:5173", "http://127.0.0.1:5173"]


settings = Settings()

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
    if "dev123" in settings.database_url:
        problems.append("数据库密码不能使用默认值 dev123")
    if problems:
        raise RuntimeError("部署安全基线未通过: " + "; ".join(problems))
