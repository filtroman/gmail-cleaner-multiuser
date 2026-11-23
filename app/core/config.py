"""
Application Configuration
-------------------------
Central configuration and settings for the application.
"""

import os
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    # Server
    app_name: str = "GMail Cleaner"
    app_version: str = "2.0.1"
    debug: bool = False
    port: int = 8766
    oauth_port: int = 8767
    oauth_external_port: int | None = Field(
        default=None,
        description="Deprecated - kept for backwards compatibility",
    )

    # Auth
    web_auth: bool = Field(
        default=False,
        description="Enable web-based authentication mode",
    )
    oauth_host: str = Field(
        default="localhost",
        description="Custom host for OAuth redirect (e.g., your domain or IP)",
    )
    oauth_redirect_uri: str | None = Field(
        default=None,
        description="Explicit full OAuth redirect URI (e.g., https://gmail.yourdomain.com/oauth/callback)",
    )

    @field_validator("web_auth", mode="before")
    @classmethod
    def validate_web_auth(cls, v) -> bool:
        """Convert string environment variable to boolean (case-insensitive)."""
        if isinstance(v, bool):
            return v
        if isinstance(v, str):
            normalized = v.lower().strip()
            return normalized in ("true", "1", "yes", "on")
        return bool(v)

    credentials_file: str = "credentials.json"
    token_file: str = "token.json"
    data_dir: str = "."
    tokens_dir: str = "tokens"
    accounts_file: str = "accounts.json"

    def __init__(self, **kwargs):
        """Initialize settings and auto-detect /app/data directory for persistence."""
        super().__init__(**kwargs)

        if os.path.exists("/app/data") and os.path.isdir("/app/data"):
            base_dir = os.path.abspath(os.path.realpath("/app/data"))
        else:
            base_dir = os.path.abspath(os.path.realpath("."))

        self.data_dir = base_dir
        self.tokens_dir = os.path.join(base_dir, "tokens")
        self.accounts_file = os.path.join(base_dir, "accounts.json")
        os.makedirs(self.tokens_dir, exist_ok=True)

        # Keep legacy token_file resolved inside base_dir for auto-migration
        name = os.path.basename(self.token_file)
        if name in ("", "."):
            name = "token.json"
        self.token_file = os.path.join(base_dir, name)

    # Gmail API
    scopes: list[str] = [
        "https://www.googleapis.com/auth/gmail.readonly",
        "https://www.googleapis.com/auth/gmail.modify",
    ]

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
    )


# Global settings instance
settings = Settings()
