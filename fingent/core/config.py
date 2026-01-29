"""
Configuration management for Fingent.

Supports:
- Local development: .env file
- AWS deployment: Secrets Manager (future)
- YAML config for business rules
"""

import os
from functools import lru_cache
from pathlib import Path
from typing import Any, Optional

import yaml
from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings loaded from environment variables."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # ==============================================
    # Environment
    # ==============================================
    fingent_env: str = Field(default="local", description="Environment: local/aws")
    log_level: str = Field(default="INFO")
    timezone: str = Field(default="America/New_York")

    # ==============================================
    # LLM API Keys
    # ==============================================
    dashscope_api_key: Optional[str] = Field(default=None, description="Qwen API Key")
    deepseek_api_key: Optional[str] = Field(default=None, description="DeepSeek API Key")

    # LLM 配置
    llm_provider: str = Field(default="deepseek", description="Primary LLM provider")
    llm_model: str = Field(default="deepseek-chat", description="Primary LLM model")

    # ==============================================
    # Data Source API Keys
    # ==============================================
    fred_api_key: Optional[str] = Field(default=None)
    finnhub_api_key: Optional[str] = Field(default=None)
    alphavantage_api_key: Optional[str] = Field(default=None)
    twelve_data_api_key: Optional[str] = Field(default=None)

    # OKX
    okx_api_key: Optional[str] = Field(default=None)
    okx_secret_key: Optional[str] = Field(default=None)
    okx_passphrase: Optional[str] = Field(default=None)
    okx_demo_trading: bool = Field(default=True)

    # Polymarket (Optional)
    polymarket_api_key: Optional[str] = Field(default=None)
    polymarket_private_key: Optional[str] = Field(default=None)
    polymarket_enabled: bool = Field(default=False)

    # Polygon.io / Massive (美股实时数据)
    polygon_api_key: Optional[str] = Field(default=None, description="Polygon.io/Massive API Key")

    # ==============================================
    # News API Keys (用于套利检测触发)
    # ==============================================
    marketaux_api_key: Optional[str] = Field(default=None, description="Marketaux API Key")
    fmp_api_key: Optional[str] = Field(default=None, description="Financial Modeling Prep API Key")
    gnews_api_key: Optional[str] = Field(default=None, description="GNews API Key")

    # ==============================================
    # Database
    # ==============================================
    database_url: str = Field(default="sqlite:///data/fingent.db")

    # ==============================================
    # Telegram
    # ==============================================
    telegram_bot_token: Optional[str] = Field(default=None)
    telegram_chat_id: Optional[str] = Field(default=None)
    telegram_enabled: bool = Field(default=False)

    # ==============================================
    # Runtime Config
    # ==============================================
    cache_ttl: int = Field(default=3600, description="Cache TTL in seconds")
    http_timeout: int = Field(default=30, description="HTTP timeout in seconds")

    @field_validator("log_level")
    @classmethod
    def validate_log_level(cls, v: str) -> str:
        valid_levels = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
        v = v.upper()
        if v not in valid_levels:
            raise ValueError(f"Invalid log level: {v}. Must be one of {valid_levels}")
        return v

    @property
    def is_local(self) -> bool:
        return self.fingent_env == "local"

    @property
    def is_aws(self) -> bool:
        return self.fingent_env == "aws"


class ConfigLoader:
    """
    Configuration loader that supports multiple sources.

    - local: Load from .env file
    - aws: Load from AWS Secrets Manager (future)
    """

    def __init__(self, env: Optional[str] = None):
        self.env = env or os.getenv("FINGENT_ENV", "local")

    def load(self) -> Settings:
        """Load settings based on environment."""
        if self.env == "local":
            return self._load_from_dotenv()
        elif self.env == "aws":
            return self._load_from_aws()
        else:
            return self._load_from_dotenv()

    def _load_from_dotenv(self) -> Settings:
        """Load from .env file (default for local development)."""
        return Settings()

    def _load_from_aws(self) -> Settings:
        """
        Load from AWS Secrets Manager.

        TODO: Implement when deploying to AWS
        """
        # Future: Use boto3 to fetch secrets
        # For now, fall back to .env
        return Settings()


@lru_cache()
def get_settings() -> Settings:
    """Get cached settings instance."""
    loader = ConfigLoader()
    return loader.load()


def load_yaml_config(config_path: Optional[str] = None) -> dict[str, Any]:
    """
    Load YAML configuration file.

    Args:
        config_path: Path to config file. Defaults to config/config.yaml

    Returns:
        Configuration dictionary
    """
    if config_path is None:
        # Find project root (where pyproject.toml is)
        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / "pyproject.toml").exists():
                config_path = str(parent / "config" / "config.yaml")
                break
        else:
            config_path = "config/config.yaml"

    path = Path(config_path)
    if not path.exists():
        raise FileNotFoundError(f"Config file not found: {config_path}")

    with open(path, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


# Convenience function
def get_config() -> dict[str, Any]:
    """Get the full configuration (settings + YAML config)."""
    settings = get_settings()
    yaml_config = load_yaml_config()
    return {
        "settings": settings,
        "config": yaml_config,
    }
