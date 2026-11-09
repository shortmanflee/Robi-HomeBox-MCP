"""Runtime configuration loaded from environment variables (and an optional .env file)."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pydantic import Field, SecretStr, field_validator, model_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# Secrets that may alternatively be supplied as `<NAME>_FILE=/run/secrets/...` (Docker secrets).
_FILE_SECRETS = ("HOMEBOX_PASSWORD", "HOMEBOX_API_KEY", "MCP_AUTH_TOKENS")

MIN_TOKEN_LENGTH = 16


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore", case_sensitive=False)

    # --- HomeBox connection -------------------------------------------------
    homebox_url: str = Field(description="Base URL of HomeBox, e.g. http://homebox:7745")
    homebox_username: str | None = None
    homebox_password: SecretStr | None = None
    homebox_api_key: SecretStr | None = Field(
        default=None, description="HomeBox API key; alternative to username/password"
    )
    homebox_verify_ssl: bool = True
    homebox_timeout: float = 30.0

    # --- MCP server -----------------------------------------------------------
    mcp_transport: Literal["http", "stdio"] = "http"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8000
    mcp_path: str = "/mcp"
    mcp_auth_tokens: SecretStr | None = Field(
        default=None, description="Comma-separated bearer tokens accepted from MCP clients"
    )
    mcp_allowed_hosts: str | None = Field(
        default=None,
        description="Optional comma-separated Host header allowlist (DNS-rebinding protection)",
    )
    mcp_read_only: bool = False
    mcp_tool_search: Literal["off", "bm25", "regex"] = Field(
        default="off",
        description="Collapse the tool list into search_tools + call_tool for clients "
        "without native tool search",
    )
    mcp_tool_search_max_results: int = Field(default=5, ge=1, le=32)
    mcp_max_attachment_mb: float = 10.0
    mcp_allow_url_fetch: bool = Field(
        default=True,
        description="Expose upload_attachment_from_url (public HTTPS only, SSRF-guarded)",
    )
    log_level: str = "INFO"

    @field_validator("homebox_url")
    @classmethod
    def _strip_url(cls, v: str) -> str:
        v = v.strip().rstrip("/")
        if v.endswith("/api"):
            v = v[: -len("/api")]
        if not v.startswith(("http://", "https://")):
            raise ValueError("HOMEBOX_URL must start with http:// or https://")
        return v

    @field_validator("mcp_path")
    @classmethod
    def _norm_path(cls, v: str) -> str:
        return "/" + v.strip().strip("/")

    @model_validator(mode="after")
    def _check_combinations(self) -> Settings:
        if self.homebox_api_key is None and not (self.homebox_username and self.homebox_password):
            raise ValueError(
                "Set either HOMEBOX_API_KEY or both HOMEBOX_USERNAME and HOMEBOX_PASSWORD"
            )
        if self.mcp_transport == "http":
            tokens = self.auth_tokens
            if not tokens:
                raise ValueError(
                    "MCP_AUTH_TOKENS is required for the http transport "
                    "(generate one with: openssl rand -hex 32)"
                )
            if any(len(t) < MIN_TOKEN_LENGTH for t in tokens):
                raise ValueError(f"Each MCP auth token must be at least {MIN_TOKEN_LENGTH} chars")
        return self

    @property
    def auth_tokens(self) -> list[str]:
        if self.mcp_auth_tokens is None:
            return []
        raw = self.mcp_auth_tokens.get_secret_value()
        return [t.strip() for t in raw.split(",") if t.strip()]

    @property
    def allowed_hosts(self) -> list[str] | None:
        if not self.mcp_allowed_hosts:
            return None
        return [h.strip() for h in self.mcp_allowed_hosts.split(",") if h.strip()]

    @property
    def max_attachment_bytes(self) -> int:
        return int(self.mcp_max_attachment_mb * 1024 * 1024)


def load_settings(**overrides: object) -> Settings:
    """Build Settings, resolving `<SECRET>_FILE` env vars into their secret values."""
    file_values: dict[str, object] = {}
    for name in _FILE_SECRETS:
        file_path = os.environ.get(f"{name}_FILE")
        if file_path and not os.environ.get(name):
            file_values[name.lower()] = Path(file_path).read_text().strip()
    return Settings(**{**file_values, **overrides})  # type: ignore[arg-type]
