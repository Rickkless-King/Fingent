"""
Logging configuration for Fingent.

Supports:
- Local: Human-readable format
- Cloud: JSON format for CloudWatch
"""

import logging
import logging.config
import os
import sys
from pathlib import Path
from typing import Optional

import yaml


def setup_logging(
    config_path: Optional[str] = None,
    log_level: Optional[str] = None,
) -> None:
    """
    Setup logging configuration.

    Args:
        config_path: Path to logging.yaml. Auto-detected if not provided.
        log_level: Override log level from environment.
    """
    env = os.getenv("FINGENT_ENV", "local")
    level = log_level or os.getenv("LOG_LEVEL", "INFO")

    # Ensure log directory exists
    log_dir = Path("data/logs")
    log_dir.mkdir(parents=True, exist_ok=True)

    # Try to load from YAML config
    if config_path is None:
        current = Path(__file__).resolve()
        for parent in current.parents:
            if (parent / "pyproject.toml").exists():
                config_path = str(parent / "config" / "logging.yaml")
                break

    if config_path and Path(config_path).exists():
        with open(config_path, "r", encoding="utf-8") as f:
            config = yaml.safe_load(f)

        # Ensure log file directory exists
        for handler in config.get("handlers", {}).values():
            if "filename" in handler:
                Path(handler["filename"]).parent.mkdir(parents=True, exist_ok=True)

        logging.config.dictConfig(config)
    else:
        # Fallback to basic configuration
        _setup_basic_logging(env, level)

    # Apply log level override
    logging.getLogger("fingent").setLevel(getattr(logging, level.upper()))


def _setup_basic_logging(env: str, level: str) -> None:
    """Setup basic logging when YAML config is not available."""
    if env == "local":
        fmt = "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s"
    else:
        # JSON format for cloud
        fmt = '{"time": "%(asctime)s", "level": "%(levelname)s", "logger": "%(name)s", "message": "%(message)s"}'

    logging.basicConfig(
        level=getattr(logging, level.upper()),
        format=fmt,
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=[logging.StreamHandler(sys.stdout)],
    )


def get_logger(name: str) -> logging.Logger:
    """
    Get a logger with the given name.

    Args:
        name: Logger name. Will be prefixed with 'fingent.' if not already.

    Returns:
        Logger instance
    """
    if not name.startswith("fingent"):
        name = f"fingent.{name}"
    return logging.getLogger(name)


class LoggerMixin:
    """Mixin class to add logging capability to any class."""

    @property
    def logger(self) -> logging.Logger:
        """Get logger for this class."""
        if not hasattr(self, "_logger"):
            self._logger = get_logger(self.__class__.__name__)
        return self._logger
