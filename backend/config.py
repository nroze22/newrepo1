"""
Application Configuration

Centralized configuration management with validation and environment-specific settings.
"""

import os
from typing import Optional
from pydantic import BaseSettings, Field, validator
from dotenv import load_dotenv

# Load environment variables
load_dotenv()


class Settings(BaseSettings):
    """Application settings with validation."""

    # Application
    APP_NAME: str = "AI Consultant Platform"
    APP_VERSION: str = "1.0.0"
    ENVIRONMENT: str = Field(default="development", env="ENVIRONMENT")
    DEBUG: bool = Field(default=False, env="DEBUG")

    # Server
    HOST: str = Field(default="0.0.0.0", env="HOST")
    PORT: int = Field(default=8000, env="PORT")
    SECRET_KEY: str = Field(..., env="SECRET_KEY")

    # Gemini API
    GEMINI_API_KEY: str = Field(..., env="GEMINI_API_KEY")
    GEMINI_MODEL: str = Field(default="gemini-1.5-pro-latest", env="GEMINI_MODEL")

    # Analysis Settings
    FRAMES_PER_ANALYSIS: int = Field(default=30, env="FRAMES_PER_ANALYSIS")
    MAX_SESSION_DURATION: int = Field(default=3600, env="MAX_SESSION_DURATION")  # seconds
    ENABLE_CACHE: bool = Field(default=True, env="ENABLE_CACHE")
    CACHE_TTL: int = Field(default=300, env="CACHE_TTL")  # seconds

    # Rate Limiting
    RATE_LIMIT_ENABLED: bool = Field(default=True, env="RATE_LIMIT_ENABLED")
    RATE_LIMIT_PER_MINUTE: int = Field(default=30, env="RATE_LIMIT_PER_MINUTE")
    RATE_LIMIT_PER_HOUR: int = Field(default=500, env="RATE_LIMIT_PER_HOUR")

    # Redis (for caching and rate limiting)
    REDIS_URL: Optional[str] = Field(default=None, env="REDIS_URL")
    REDIS_ENABLED: bool = Field(default=False, env="REDIS_ENABLED")

    # Stripe
    STRIPE_SECRET_KEY: Optional[str] = Field(default=None, env="STRIPE_SECRET_KEY")
    STRIPE_PUBLISHABLE_KEY: Optional[str] = Field(default=None, env="STRIPE_PUBLISHABLE_KEY")

    # Monitoring
    SENTRY_DSN: Optional[str] = Field(default=None, env="SENTRY_DSN")
    SENTRY_ENABLED: bool = Field(default=False, env="SENTRY_ENABLED")

    # CORS
    CORS_ORIGINS: str = Field(default="*", env="CORS_ORIGINS")

    # Logging
    LOG_LEVEL: str = Field(default="INFO", env="LOG_LEVEL")
    LOG_FORMAT: str = Field(default="json", env="LOG_FORMAT")  # json or text

    # WebSocket
    WS_HEARTBEAT_INTERVAL: int = Field(default=30, env="WS_HEARTBEAT_INTERVAL")
    WS_MESSAGE_SIZE_LIMIT: int = Field(default=10 * 1024 * 1024, env="WS_MESSAGE_SIZE_LIMIT")  # 10MB

    @validator("GEMINI_API_KEY")
    def validate_gemini_key(cls, v):
        """Validate Gemini API key is not a placeholder."""
        if not v or v.startswith("your_"):
            raise ValueError(
                "GEMINI_API_KEY not configured. "
                "Get your key from https://makersuite.google.com/app/apikey"
            )
        return v

    @validator("SECRET_KEY")
    def validate_secret_key(cls, v, values):
        """Validate secret key in production."""
        env = values.get("ENVIRONMENT", "development")
        if env == "production" and (not v or v.startswith("your_") or len(v) < 32):
            raise ValueError(
                "SECRET_KEY must be a strong random string in production. "
                "Generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))'"
            )
        return v

    @validator("CORS_ORIGINS")
    def validate_cors(cls, v, values):
        """Validate CORS configuration in production."""
        env = values.get("ENVIRONMENT", "development")
        if env == "production" and v == "*":
            raise ValueError(
                "CORS_ORIGINS should not be '*' in production. "
                "Specify allowed origins explicitly."
            )
        return v

    @property
    def cors_origins_list(self) -> list:
        """Get CORS origins as a list."""
        if self.CORS_ORIGINS == "*":
            return ["*"]
        return [origin.strip() for origin in self.CORS_ORIGINS.split(",")]

    @property
    def is_production(self) -> bool:
        """Check if running in production."""
        return self.ENVIRONMENT == "production"

    @property
    def is_development(self) -> bool:
        """Check if running in development."""
        return self.ENVIRONMENT == "development"

    class Config:
        env_file = ".env"
        case_sensitive = True


# Global settings instance
try:
    settings = Settings()
except Exception as e:
    print(f"❌ Configuration Error: {str(e)}")
    print("\n💡 Please check your .env file and ensure all required variables are set.")
    print("   Required variables:")
    print("   - GEMINI_API_KEY (get from https://makersuite.google.com/app/apikey)")
    print("   - SECRET_KEY (generate with: python -c 'import secrets; print(secrets.token_urlsafe(32))')")
    raise SystemExit(1)
