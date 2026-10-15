import os
from typing import List
from dotenv import load_dotenv

load_dotenv()


class AppConfig:
    APP_NAME: str = "Mind Map Generator API"
    FRONTEND_URL: str = os.getenv("FRONTEND_URL", "http://localhost:3000")
    ALLOWED_ORIGINS: List[str] = [
        FRONTEND_URL,
        "http://localhost:3000",
        "http://localhost:5173",
        "http://127.0.0.1:3000",
        "http://127.0.0.1:5173",
    ]
    
    # Redis & Worker Settings
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379")
    BACKEND_INTERNAL_URL: str = os.getenv("BACKEND_INTERNAL_URL", os.getenv("CALLBACK_BASE_URL", "http://localhost:8000"))
    
    # Cookie Configuration
    COOKIE_ACCESS_TOKEN_KEY: str = "access_token"
    COOKIE_REFRESH_TOKEN_KEY: str = "refresh_token"
    COOKIE_SECURE: bool = os.getenv("COOKIE_SECURE", "false").lower() in ("true", "1")
    COOKIE_SAMESITE: str = os.getenv("COOKIE_SAMESITE", "lax")  # 'lax', 'strict', or 'none'
    COOKIE_DOMAIN: str = os.getenv("COOKIE_DOMAIN", None)


settings = AppConfig()
