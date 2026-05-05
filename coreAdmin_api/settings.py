from pydantic_settings import BaseSettings, SettingsConfigDict
import json


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    DATABASE_URL: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/coreadmin"

    SECRET_KEY: str = "change-me-in-production"
    ALGORITHM: str = "HS256"
    ACCESS_TOKEN_EXPIRE_MINUTES: int = 15
    REFRESH_TOKEN_EXPIRE_DAYS: int = 7

    CORS_ORIGINS: list[str] = ["http://localhost:3000"]

    DEBUG: bool = False
    MULTI_TENANT: bool = False
    # "header" uses X-Tenant-Slug; "subdomain" extracts from the first hostname segment
    TENANT_RESOLUTION_STRATEGY: str = "header"

    ENABLE_BUILTIN_ADMIN_UI: bool = True
    ADMIN_UI_PATH: str = "/admin-ui"

    ENABLE_WORKFLOWS: bool = True

    # Step-up window: how recent a login must be for protected actions (minutes)
    STEP_UP_WINDOW_MINUTES: int = 15


settings = Settings()
