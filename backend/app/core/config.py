from __future__ import annotations

import os

try:
    from dotenv import load_dotenv

    load_dotenv()
except ImportError:
    pass


class AppConfig:
    APP_NAME: str = "Mind Map Generator API"
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")
    ALLOWED_ORIGINS: list[str] = [
        FRONTEND_URL,
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]

    # Redis & Worker Settings
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    BACKEND_INTERNAL_URL: str = os.getenv(
        "BACKEND_INTERNAL_URL", os.getenv("CALLBACK_BASE_URL", "http://localhost:8000")
    )

    # Cookie Configuration
    COOKIE_ACCESS_TOKEN_KEY: str = "access_token"
    COOKIE_REFRESH_TOKEN_KEY: str = "refresh_token"
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "false").lower() in ("true", "1")
    COOKIE_SAMESITE: str = os.getenv("COOKIE_SAMESITE", "lax")  # 'lax', 'strict', or 'none'
    COOKIE_DOMAIN: str | None = os.getenv("COOKIE_DOMAIN") or None

    # Export & Conversion Settings (S2-BE6 / US-07)
    EXPORT_CONVERTER_URL: str = os.getenv("EXPORT_CONVERTER_URL", "http://localhost:3001/convert")
    EXPORT_TIMEOUT_SECONDS: float = float(os.getenv("EXPORT_TIMEOUT_SECONDS", "15.0"))

    # Rate Limiting & Quota Settings (S3-BE5 / FR-41)
    GEN_RATE_LIMIT_PER_HOUR: int = int(os.getenv("GEN_RATE_LIMIT_PER_HOUR", "30"))
    GEN_RATE_LIMIT_WINDOW_S: int = int(os.getenv("GEN_RATE_LIMIT_WINDOW_S", "3600"))
    CHAT_RATE_LIMIT_PER_MINUTE: int = int(os.getenv("CHAT_RATE_LIMIT_PER_MINUTE", "20"))
    CHAT_RATE_LIMIT_WINDOW_S: int = int(os.getenv("CHAT_RATE_LIMIT_WINDOW_S", "60"))
    MEMORY_PROPOSE_RATE_LIMIT_PER_MINUTE: int = int(os.getenv("MEMORY_PROPOSE_RATE_LIMIT_PER_MINUTE", "30"))
    MEMORY_PROPOSE_RATE_LIMIT_WINDOW_S: int = int(os.getenv("MEMORY_PROPOSE_RATE_LIMIT_WINDOW_S", "60"))
    # Memory Settings (S3-BE4)
    MEMORY_PER_USER_LIMIT: int = int(os.getenv("MEMORY_PER_USER_LIMIT", "500"))
    # Internal Service Authentication (AUD-01 / NEW-01 / S3-BE4 / S3-BE8)
    # Must be explicitly configured. In test environments without env var, falls back to test key.
    INTERNAL_SERVICE_KEY: str = os.getenv(
        "INTERNAL_SERVICE_KEY",
        "mindweave-test-internal-secret" if os.getenv("ENVIRONMENT") == "test" else "",
    )


settings = AppConfig()
