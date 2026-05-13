import warnings
from functools import lru_cache
from pydantic import model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_DEFAULT_SECRET_KEY = "change-me-in-production"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/adminfoundry"

    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    REDIS_URL: str | None = None

    # GDPR / compliance
    AUDIT_LOG_RETENTION_DAYS: int = 90  # 0 = keep forever
    PASSWORD_MIN_LENGTH: int = 8

    # Cleanup task (caller must still wire `asyncio.create_task(periodic_cleanup())`)
    ENABLE_CLEANUP_TASK: bool = True
    CLEANUP_INTERVAL_SECONDS: int = 3600

    # 2FA / TOTP
    ENFORCE_2FA_FOR_SUPERADMIN: bool = False
    TOTP_ISSUER: str = "adminfoundry"

    DEBUG: bool = False
    MULTI_TENANT: bool = False
    # "header" uses X-Tenant-Slug; "subdomain" extracts from the first hostname segment
    TENANT_RESOLUTION_STRATEGY: str = "header"

    ENABLE_BUILTIN_ADMIN_UI: bool = True
    ADMIN_UI_PATH: str = "/admin-ui"
    ADMIN_TITLE: str = "adminfoundry"

    # Step-up window: how recent a login must be for protected actions (minutes)
    STEP_UP_WINDOW_MINUTES: int = 15

    # Account lockout after repeated login failures
    LOGIN_MAX_FAILURES: int = 5
    LOGIN_LOCKOUT_MINUTES: int = 15

    LOG_JSON: bool = True

    # Email / Password-Reset
    EMAIL_HOST: str = ""
    EMAIL_PORT: int = 587
    EMAIL_HOST_USER: str = ""
    EMAIL_HOST_PASSWORD: str = ""
    EMAIL_USE_TLS: bool = True
    EMAIL_DEFAULT_FROM: str = "noreply@example.com"
    PASSWORD_RESET_TIMEOUT_MINUTES: int = 30
    PASSWORD_RESET_ENABLED: bool = True

    @model_validator(mode="after")
    def _check_secret_key(self) -> "Settings":
        if self.SECRET_KEY == _DEFAULT_SECRET_KEY:
            warnings.warn(
                "SECRET_KEY is set to the default insecure value. "
                "Set SECRET_KEY to a random secret before deploying to production.",
                stacklevel=2,
            )
        return self


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


settings = get_settings()
