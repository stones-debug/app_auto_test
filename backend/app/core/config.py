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

    # Agent
    agent_heartbeat_interval: int = 30
    agent_heartbeat_timeout: int = 120
    min_agent_version: str = "1.0.0"
    internal_token: str = "dev-internal-token-change-me"
    backend_base_url: str = "http://127.0.0.1:8001"

    # Worker
    worker_poll_interval: int = 2
    worker_claim_stale_minutes: int = 10

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
