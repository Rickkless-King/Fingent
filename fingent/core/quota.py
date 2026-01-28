"""
Quota management for provider API usage.

Tracks per-minute and per-day limits for providers based on config.yaml.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from typing import Optional

from cachetools import TTLCache

from fingent.core.config import load_yaml_config


@dataclass
class QuotaCheckResult:
    allowed: bool
    reason: str = ""


class QuotaManager:
    """
    Simple in-memory quota tracker.

    Limits are configured in config.yaml under usage_mode.quotas.
    """

    def __init__(self, config: Optional[dict] = None):
        self.config = config or load_yaml_config()
        usage_mode = self.config.get("usage_mode", {})
        self.enabled = usage_mode.get("enabled", True)
        self.quotas = usage_mode.get("quotas", {})

        # Counters reset by TTL.
        self._per_minute = TTLCache(maxsize=512, ttl=60)
        self._per_day = TTLCache(maxsize=512, ttl=86400)

    def check_and_consume(self, provider: str, cost: int = 1) -> QuotaCheckResult:
        """
        Check quota and consume if allowed.

        Args:
            provider: Provider name
            cost: Cost units to consume
        """
        if not self.enabled:
            return QuotaCheckResult(True)

        limits = self.quotas.get(provider, {})
        if not limits:
            return QuotaCheckResult(True)

        per_minute = limits.get("per_minute")
        per_day = limits.get("per_day")

        # Check without consuming first.
        if per_minute is not None:
            used = self._per_minute.get(provider, 0)
            if used + cost > per_minute:
                return QuotaCheckResult(False, "per_minute quota exceeded")

        if per_day is not None:
            used = self._per_day.get(provider, 0)
            if used + cost > per_day:
                return QuotaCheckResult(False, "per_day quota exceeded")

        # Consume.
        if per_minute is not None:
            self._per_minute[provider] = self._per_minute.get(provider, 0) + cost
        if per_day is not None:
            self._per_day[provider] = self._per_day.get(provider, 0) + cost

        return QuotaCheckResult(True)

    def get_usage(self, provider: str) -> dict[str, int]:
        """Get current usage counters for a provider."""
        return {
            "per_minute": self._per_minute.get(provider, 0),
            "per_day": self._per_day.get(provider, 0),
        }


@lru_cache()
def get_quota_manager() -> QuotaManager:
    """Get a cached QuotaManager instance."""
    return QuotaManager()
