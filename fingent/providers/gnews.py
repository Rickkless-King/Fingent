"""
GNews provider for general news.

Provides:
- Top headlines across categories
- News search by keyword
- Multi-language support

Free tier: 100 requests/day, 1 request/second
Docs: https://gnews.io/docs/v4
"""

import time
from datetime import datetime, timedelta
from typing import Any, Optional

from fingent.core.errors import ProviderError
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.models import NewsItem
from fingent.providers.base import BaseProvider, HealthCheckResult, ProviderStatus


class GNewsProvider(BaseProvider):
    """
    GNews general news provider.

    Good for broad news coverage, supports keyword search.

    Free tier limitations:
    - 100 API calls/day
    - 1 request/second rate limit
    - Non-commercial use only
    """

    name = "gnews"
    BASE_URL = "https://gnews.io/api/v4"

    # Supported categories for top-headlines
    CATEGORIES = [
        "general",
        "world",
        "nation",
        "business",
        "technology",
        "entertainment",
        "sports",
        "science",
        "health",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._api_key: Optional[str] = None
        self._last_request_time: float = 0

    def _initialize(self) -> None:
        """Initialize GNews client."""
        self._api_key = self.settings.gnews_api_key
        if not self._api_key:
            self.logger.warning("GNews API key not configured")
        else:
            self.logger.info("GNews provider initialized")

    @property
    def is_configured(self) -> bool:
        """Check if API key is configured."""
        self._ensure_initialized()
        return bool(self._api_key)

    def _rate_limit(self) -> None:
        """Enforce 1 request/second rate limit."""
        now = time.time()
        elapsed = now - self._last_request_time
        if elapsed < 1.0:
            time.sleep(1.0 - elapsed)
        self._last_request_time = time.time()

    def healthcheck(self) -> HealthCheckResult:
        """Check GNews API health."""
        if not self.is_configured:
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message="GNews API key not configured",
            )

        start_time = time.time()

        try:
            self._rate_limit()
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/top-headlines",
                params={
                    "apikey": self._api_key,
                    "lang": "en",
                    "max": 1,
                },
            )

            latency = (time.time() - start_time) * 1000

            return HealthCheckResult(
                status=ProviderStatus.HEALTHY,
                message="GNews API is responding",
                latency_ms=latency,
            )

        except Exception as e:
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message=f"GNews API error: {e}",
            )

    def get_top_headlines(
        self,
        category: str = "business",
        language: str = "en",
        country: Optional[str] = None,
        limit: int = 10,
    ) -> list[NewsItem]:
        """
        Get top headlines.

        Args:
            category: News category (business, technology, etc.)
            language: Language code (default: "en")
            country: Country code (optional)
            limit: Maximum articles (max 10 for free tier)

        Returns:
            List of NewsItem objects
        """
        if not self.is_configured:
            self.logger.warning("GNews not configured, returning empty")
            return []

        if category not in self.CATEGORIES:
            category = "business"

        cache_key = f"headlines:{category}:{language}:{country}:{limit}"
        cached = self._get_cached(cache_key)
        if cached:
            return [NewsItem.from_dict(n) for n in cached]

        try:
            self._rate_limit()
            params = {
                "apikey": self._api_key,
                "category": category,
                "lang": language,
                "max": min(limit, 10),
            }

            if country:
                params["country"] = country

            self._consume_quota()
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/top-headlines",
                params=params,
            )

            articles = response.get("articles", [])
            result = self._parse_articles(articles)

            self._set_cached(cache_key, [n.to_dict() for n in result])
            self.logger.info(f"Fetched {len(result)} headlines from GNews ({category})")
            return result

        except Exception as e:
            self.logger.warning(f"Failed to get headlines from GNews: {e}")
            return []

    def search_news(
        self,
        keywords: list[str],
        language: str = "en",
        limit: int = 10,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> list[NewsItem]:
        """
        Search news by keywords.

        Args:
            keywords: Keywords to search (will be joined with OR)
            language: Language code (default: "en")
            limit: Maximum articles (max 10 for free tier)
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)

        Returns:
            List of NewsItem objects
        """
        if not self.is_configured:
            self.logger.warning("GNews not configured, returning empty")
            return []

        # Build query: join keywords with OR
        query = " OR ".join(keywords[:5])  # Limit to 5 keywords

        cache_key = f"search:{query}:{language}:{limit}"
        cached = self._get_cached(cache_key)
        if cached:
            return [NewsItem.from_dict(n) for n in cached]

        try:
            self._rate_limit()
            params = {
                "apikey": self._api_key,
                "q": query,
                "lang": language,
                "max": min(limit, 10),
            }

            if from_date:
                params["from"] = from_date
            if to_date:
                params["to"] = to_date

            self._consume_quota()
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/search",
                params=params,
            )

            articles = response.get("articles", [])
            result = self._parse_articles(articles)

            self._set_cached(cache_key, [n.to_dict() for n in result])
            self.logger.info(f"Searched {len(result)} articles from GNews for: {query[:50]}")
            return result

        except Exception as e:
            self.logger.warning(f"Failed to search news from GNews: {e}")
            return []

    def get_market_news(self, limit: int = 10) -> list[NewsItem]:
        """
        Get market/business news.

        Args:
            limit: Maximum number of articles

        Returns:
            List of NewsItem objects
        """
        return self.get_top_headlines(category="business", limit=limit)

    def _parse_articles(self, articles: list[dict]) -> list[NewsItem]:
        """Parse GNews article format to NewsItem."""
        result = []

        for article in articles:
            # Parse publication date
            published_at = article.get("publishedAt", "")
            if published_at:
                try:
                    # GNews uses ISO format
                    published_at = published_at.replace("Z", "+00:00")
                except Exception:
                    pass

            # Extract source
            source_info = article.get("source", {})
            source = source_info.get("name", "unknown") if isinstance(source_info, dict) else "unknown"

            item = NewsItem(
                title=article.get("title", ""),
                summary=article.get("description", "") or article.get("content", ""),
                url=article.get("url", ""),
                published_at=published_at,
                source=source,
                sentiment_score=None,  # GNews doesn't provide sentiment
                sentiment_label=None,
                tickers=[],  # GNews doesn't tag tickers
                topics=[],
                provider=self.name,
            )
            result.append(item)

        return result
