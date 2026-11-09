"""Load and validate The Relay's runtime configuration."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

import yaml
from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


PROJECT_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG_PATH = PROJECT_ROOT / "config" / "default.yaml"


class ProviderConfig(BaseModel):
    """Endpoint settings for one OpenAI-compatible provider."""

    model_config = ConfigDict(extra="forbid")

    base_url: str = Field(min_length=1)


class GroqConfig(ProviderConfig):
    api_key_env: str = Field(min_length=1)
    model: str | None = None

    @field_validator("base_url")
    @classmethod
    def require_official_groq_endpoint(cls, value: str) -> str:
        parsed = urlsplit(value)
        try:
            port = parsed.port
        except ValueError as exc:
            raise ValueError("Groq base_url has an invalid port") from exc
        if (
            parsed.scheme != "https"
            or parsed.hostname != "api.groq.com"
            or port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
            or parsed.path.rstrip("/") != "/openai/v1"
            or parsed.query
            or parsed.fragment
        ):
            raise ValueError(
                "Groq base_url must be https://api.groq.com/openai/v1"
            )
        return value.rstrip("/")


class AppConfig(BaseModel):
    """Validated settings needed by the first pipeline modules."""

    model_config = ConfigDict(extra="forbid")

    provider: Literal["groq", "standin"] = "standin"
    request_timeout_s: float = Field(default=30, gt=0)
    stream: bool = True
    groq: GroqConfig
    standin: ProviderConfig

    @model_validator(mode="after")
    def require_selected_model_for_groq(self) -> AppConfig:
        if self.provider == "groq" and not self.groq.model:
            raise ValueError(
                "Set groq.model only after decision 0002 selects a model."
            )
        return self

    @property
    def api_key(self) -> str | None:
        """Read a key from the configured environment variable, never YAML."""
        if self.provider != "groq":
            return None
        key = os.environ.get(self.groq.api_key_env, "").strip()
        if not key:
            raise RuntimeError(
                f"Missing {self.groq.api_key_env}; add it to the ignored .env file."
            )
        return key

    @property
    def active_base_url(self) -> str:
        if self.provider == "groq":
            return self.groq.base_url.rstrip("/")
        return self.standin.base_url.rstrip("/")


def load_config(path: str | Path | None = None) -> AppConfig:
    """Read YAML from *path* or config/default.yaml and validate its fields."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    try:
        with config_path.open("r", encoding="utf-8") as stream:
            raw = yaml.safe_load(stream)
    except OSError as exc:
        raise RuntimeError(f"Cannot read config file {config_path}: {exc}") from exc
    except yaml.YAMLError as exc:
        raise RuntimeError(f"Invalid YAML in {config_path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise RuntimeError(f"Config root must be a YAML mapping: {config_path}")
    try:
        return AppConfig.model_validate(raw)
    except Exception as exc:
        raise RuntimeError(f"Invalid configuration in {config_path}: {exc}") from exc
