"""Platform configuration loaded from environment variables."""

import logging
import uuid
from functools import lru_cache

from pydantic import model_validator
from pydantic_settings import BaseSettings

logger = logging.getLogger(__name__)

_INSECURE_SECRETS = {"change-me-in-production", "secret", "changeme", ""}
_MIN_JWT_SECRET_LENGTH = 32


class PlatformConfig(BaseSettings):
    """Platform-level configuration from env vars."""

    model_config = {"env_prefix": "CRAB_"}

    # PostgreSQL
    database_url: str = "postgresql+asyncpg://postgres:postgres@localhost:5432/crab"

    # Redis
    redis_url: str = "redis://localhost:6379/0"

    # JWT
    jwt_secret: str = "change-me-in-production"
    jwt_algorithm: str = "HS256"
    jwt_access_token_expire_minutes: int = 60
    jwt_refresh_token_expire_days: int = 30

    # Gateway instance ID (for run crash recovery).
    # Defaults to a random UUID so each process gets a unique ID automatically.
    instance_id: str = ""

    @model_validator(mode="after")
    def _validate_platform_config(self) -> "PlatformConfig":
        # JWT secret validation
        if self.jwt_secret in _INSECURE_SECRETS:
            raise ValueError(
                "CRAB_JWT_SECRET is unset or insecure. "
                "Set a strong secret (>= 32 characters) via the CRAB_JWT_SECRET environment variable."
            )
        if len(self.jwt_secret) < _MIN_JWT_SECRET_LENGTH:
            raise ValueError(
                f"CRAB_JWT_SECRET is too short ({len(self.jwt_secret)} chars). "
                f"Minimum length is {_MIN_JWT_SECRET_LENGTH} characters."
            )

        # Auto-generate unique instance_id if not explicitly set
        if not self.instance_id:
            self.instance_id = f"gateway-{uuid.uuid4().hex[:12]}"
            logger.info("Auto-generated gateway instance_id: %s", self.instance_id)

        return self

    # Object storage
    storage_backend: str = "local"  # "local", "bos", or "oss"
    storage_root: str | None = None  # Local storage root (local backend only)
    bos_access_key: str | None = None
    bos_secret_key: str | None = None
    bos_endpoint: str | None = None
    bos_bucket: str | None = None
    oss_access_key_id: str | None = None
    oss_access_key_secret: str | None = None
    oss_endpoint: str | None = None
    oss_bucket: str | None = None


@lru_cache
def get_platform_config() -> PlatformConfig:
    return PlatformConfig()
