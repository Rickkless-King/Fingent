"""
News Router - Intelligent news provider routing with fallback.

Features:
- Round-robin rotation across multiple providers
- Automatic fallback when quota exceeded or errors
- Provider health tracking
- Unified interface for all news sources

Priority order (configurable):
1. Marketaux (financial-focused, has sentiment)
2. FMP (financial data provider)
3. GNews (general news, good search)
4. Finnhub (existing, as final fallback)
"""

from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Optional, Protocol
from enum import Enum

from fingent.core.config import get_settings, load_yaml_config
from fingent.core.errors import QuotaExceededError
from fingent.core.logging import LoggerMixin
from fingent.domain.models import NewsItem


class NewsProviderProtocol(Protocol):
    """Protocol for news providers."""

    name: str

    def get_market_news(self, limit: int = 20) -> list[NewsItem]:
        ...

    def search_news(self, keywords: list[str], limit: int = 20) -> list[NewsItem]:
        ...

    @property
    def is_configured(self) -> bool:
        ...


@dataclass
class ProviderStats:
    """Statistics for a news provider."""

    name: str
    calls_today: int = 0
    errors_today: int = 0
    last_success: Optional[datetime] = None
    last_error: Optional[datetime] = None
    last_error_message: str = ""
    quota_exceeded: bool = False
    daily_limit: int = 100  # Default limit

    def record_success(self) -> None:
        """Record a successful call."""
        self.calls_today += 1
        self.last_success = datetime.now()

    def record_error(self, message: str = "") -> None:
        """Record an error."""
        self.errors_today += 1
        self.last_error = datetime.now()
        self.last_error_message = message

    def record_quota_exceeded(self) -> None:
        """Mark quota as exceeded."""
        self.quota_exceeded = True
        self.record_error("Quota exceeded")

    def reset_daily(self) -> None:
        """Reset daily counters."""
        self.calls_today = 0
        self.errors_today = 0
        self.quota_exceeded = False

    @property
    def is_available(self) -> bool:
        """Check if provider is available for use."""
        if self.quota_exceeded:
            return False
        # Consider unavailable if too many recent errors
        if self.errors_today > 5:
            return False
        return True

    @property
    def remaining_calls(self) -> int:
        """Estimate remaining calls."""
        return max(0, self.daily_limit - self.calls_today)


class NewsRouter(LoggerMixin):
    """
    Intelligent news provider router.

    Routes news requests across multiple providers with:
    - Priority-based selection
    - Automatic fallback on errors
    - Quota tracking
    - Round-robin when multiple providers available
    """

    def __init__(self):
        """Initialize the news router."""
        self.settings = get_settings()
        self.config = load_yaml_config()

        # Initialize providers lazily
        self._providers: dict[str, NewsProviderProtocol] = {}
        self._stats: dict[str, ProviderStats] = {}

        # Provider priority order (configurable)
        self._priority = self._load_priority()

        # Track last used provider for round-robin
        self._last_provider_index = -1

        # Last reset time for daily counters
        self._last_reset_date = datetime.now().date()

    def _load_priority(self) -> list[str]:
        """Load provider priority from config."""
        news_config = self.config.get("news_providers", {})
        priority = news_config.get("priority", [
            "marketaux",
            "fmp",
            "gnews",
            "finnhub",
        ])
        return priority

    def _get_provider(self, name: str) -> Optional[NewsProviderProtocol]:
        """Get or create a provider by name."""
        if name in self._providers:
            return self._providers[name]

        provider = None

        try:
            if name == "marketaux":
                from fingent.providers.marketaux import MarketauxProvider
                provider = MarketauxProvider()
            elif name == "fmp":
                from fingent.providers.fmp import FMPProvider
                provider = FMPProvider()
            elif name == "gnews":
                from fingent.providers.gnews import GNewsProvider
                provider = GNewsProvider()
            elif name == "finnhub":
                from fingent.providers.finnhub import FinnhubProvider
                provider = FinnhubProvider()

            if provider:
                # Check if provider is configured
                # Different providers have different ways to check this
                is_configured = True  # Default assume configured

                if hasattr(provider, 'is_configured'):
                    # New providers have is_configured property
                    is_configured = provider.is_configured
                elif name == "finnhub":
                    # FinnhubProvider doesn't have is_configured, check API key
                    is_configured = bool(self.settings.finnhub_api_key)

                if is_configured:
                    self._providers[name] = provider
                    self._init_stats(name)
                    return provider

        except Exception as e:
            self.logger.warning(f"Failed to initialize provider {name}: {e}")

        return None

    def _init_stats(self, name: str) -> None:
        """Initialize stats for a provider."""
        if name not in self._stats:
            # Set daily limits from config
            limits = {
                "marketaux": 100,
                "fmp": 200,  # Bandwidth-based, approximate
                "gnews": 100,
                "finnhub": 1000,  # 60/min but high daily
            }
            self._stats[name] = ProviderStats(
                name=name,
                daily_limit=limits.get(name, 100),
            )

    def _reset_daily_if_needed(self) -> None:
        """Reset daily counters if new day."""
        today = datetime.now().date()
        if today != self._last_reset_date:
            for stats in self._stats.values():
                stats.reset_daily()
            self._last_reset_date = today
            self.logger.info("Reset daily news provider counters")

    def _get_available_providers(self) -> list[str]:
        """Get list of available providers in priority order."""
        self._reset_daily_if_needed()

        available = []
        for name in self._priority:
            provider = self._get_provider(name)
            if provider and name in self._stats and self._stats[name].is_available:
                available.append(name)

        return available

    def _select_provider(self, available: list[str]) -> Optional[str]:
        """Select next provider using round-robin within priority."""
        if not available:
            return None

        # Simple round-robin
        self._last_provider_index = (self._last_provider_index + 1) % len(available)
        return available[self._last_provider_index]

    def get_market_news(self, limit: int = 20) -> list[NewsItem]:
        """
        Get market news from the best available provider.

        Args:
            limit: Maximum number of articles

        Returns:
            List of NewsItem objects
        """
        available = self._get_available_providers()

        if not available:
            self.logger.warning("No news providers available")
            return []

        # Try providers in order until one succeeds
        for name in available:
            provider = self._providers.get(name)
            if not provider:
                continue

            try:
                self.logger.debug(f"Trying {name} for market news")
                result = provider.get_market_news(limit=limit)

                if result:
                    self._stats[name].record_success()
                    self.logger.info(f"Got {len(result)} news from {name}")
                    return result

            except QuotaExceededError:
                self._stats[name].record_quota_exceeded()
                self.logger.warning(f"{name} quota exceeded, trying next")
                continue

            except Exception as e:
                self._stats[name].record_error(str(e))
                self.logger.warning(f"{name} failed: {e}, trying next")
                continue

        self.logger.warning("All news providers failed")
        return []

    def search_news(
        self,
        keywords: list[str],
        limit: int = 20,
    ) -> list[NewsItem]:
        """
        Search news by keywords across providers.

        Args:
            keywords: Keywords to search
            limit: Maximum results

        Returns:
            List of matching NewsItem objects
        """
        available = self._get_available_providers()

        if not available:
            self.logger.warning("No news providers available for search")
            return []

        for name in available:
            provider = self._providers.get(name)
            if not provider:
                continue

            try:
                self.logger.debug(f"Searching {name} for: {keywords[:3]}")
                result = provider.search_news(keywords=keywords, limit=limit)

                if result:
                    self._stats[name].record_success()
                    self.logger.info(f"Found {len(result)} articles from {name}")
                    return result

            except QuotaExceededError:
                self._stats[name].record_quota_exceeded()
                continue

            except Exception as e:
                self._stats[name].record_error(str(e))
                continue

        return []

    def get_news_from_all(
        self,
        limit_per_provider: int = 10,
    ) -> list[NewsItem]:
        """
        Aggregate news from all available providers.

        Useful for getting diverse news coverage.

        Args:
            limit_per_provider: Max articles per provider

        Returns:
            Combined list of NewsItem objects (deduplicated by URL)
        """
        all_news = []
        seen_urls = set()

        available = self._get_available_providers()

        for name in available:
            provider = self._providers.get(name)
            if not provider:
                continue

            try:
                news = provider.get_market_news(limit=limit_per_provider)
                self._stats[name].record_success()

                for item in news:
                    if item.url and item.url not in seen_urls:
                        all_news.append(item)
                        seen_urls.add(item.url)

            except Exception as e:
                self._stats[name].record_error(str(e))
                continue

        # Sort by publication time (newest first)
        all_news.sort(key=lambda x: x.published_at or "", reverse=True)

        return all_news

    def get_stats(self) -> dict[str, dict]:
        """Get statistics for all providers."""
        self._reset_daily_if_needed()

        result = {}
        for name, stats in self._stats.items():
            result[name] = {
                "calls_today": stats.calls_today,
                "errors_today": stats.errors_today,
                "remaining_calls": stats.remaining_calls,
                "quota_exceeded": stats.quota_exceeded,
                "is_available": stats.is_available,
                "last_success": stats.last_success.isoformat() if stats.last_success else None,
                "last_error": stats.last_error.isoformat() if stats.last_error else None,
            }

        return result


# Singleton instance
_news_router: Optional[NewsRouter] = None


def get_news_router() -> NewsRouter:
    """Get the global news router instance."""
    global _news_router
    if _news_router is None:
        _news_router = NewsRouter()
    return _news_router
