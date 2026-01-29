"""
Marketaux provider for financial news.

Provides:
- Real-time financial news from 5000+ sources
- Sentiment analysis included
- Entity tagging (stocks, ETFs, crypto)

Free tier: 100 requests/day
Docs: https://www.marketaux.com/documentation
"""

import time
from datetime import datetime, timedelta
from typing import Any, Optional

from fingent.core.errors import ProviderError
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.models import NewsItem
from fingent.providers.base import BaseProvider, HealthCheckResult, ProviderStatus


class MarketauxProvider(BaseProvider):
    """
    Marketaux financial news provider.

    Specialized for financial/market news with built-in sentiment.

    Free tier limitations:
    - 100 API calls/day
    - Real-time data access
    """

    name = "marketaux"
    BASE_URL = "https://api.marketaux.com/v1"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._api_token: Optional[str] = None

    def _initialize(self) -> None:
        """Initialize Marketaux client."""
        self._api_token = self.settings.marketaux_api_key
        if not self._api_token:
            self.logger.warning("Marketaux API key not configured")
        else:
            self.logger.info("Marketaux provider initialized")

    @property
    def is_configured(self) -> bool:
        """Check if API key is configured."""
        self._ensure_initialized()
        return bool(self._api_token)

    def healthcheck(self) -> HealthCheckResult:
        """Check Marketaux API health."""
        if not self.is_configured:
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message="Marketaux API key not configured",
            )

        start_time = time.time()

        try:
            # Simple request to check API
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/news/all",
                params={
                    "api_token": self._api_token,
                    "limit": 1,
                },
            )

            latency = (time.time() - start_time) * 1000

            return HealthCheckResult(
                status=ProviderStatus.HEALTHY,
                message="Marketaux API is responding",
                latency_ms=latency,
            )

        except Exception as e:
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message=f"Marketaux API error: {e}",
            )

    def get_news(
        self,
        symbols: Optional[list[str]] = None,
        filter_entities: bool = True,
        language: str = "en",
        limit: int = 20,
        published_after: Optional[str] = None,
    ) -> list[NewsItem]:
        """
        Get financial news from Marketaux.

        Args:
            symbols: Filter by stock symbols (e.g., ["AAPL", "TSLA"])
            filter_entities: Only return news with identified entities
            language: Language filter (default: "en")
            limit: Maximum number of articles (max 50)
            published_after: ISO datetime string for filtering

        Returns:
            List of NewsItem objects
        """
        if not self.is_configured:
            self.logger.warning("Marketaux not configured, returning empty")
            return []

        cache_key = f"news:{','.join(symbols or [])}:{language}:{limit}"
        cached = self._get_cached(cache_key)
        if cached:
            return [NewsItem.from_dict(n) for n in cached]

        try:
            params = {
                "api_token": self._api_token,
                "language": language,
                "limit": min(limit, 50),
                "filter_entities": "true" if filter_entities else "false",
            }

            if symbols:
                params["symbols"] = ",".join(symbols)

            if published_after:
                params["published_after"] = published_after
            else:
                # Default: last 24 hours
                yesterday = (datetime.now() - timedelta(hours=24)).strftime("%Y-%m-%dT%H:%M")
                params["published_after"] = yesterday

            self._consume_quota()
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/news/all",
                params=params,
            )

            articles = response.get("data", [])
            result = []

            for article in articles:
                # Extract sentiment from Marketaux response
                sentiment_score = None
                entities = article.get("entities", [])
                if entities:
                    # Average sentiment across all entities
                    scores = [
                        e.get("sentiment_score", 0)
                        for e in entities
                        if e.get("sentiment_score") is not None
                    ]
                    if scores:
                        sentiment_score = sum(scores) / len(scores)

                # Extract tickers
                tickers = [
                    e.get("symbol", "")
                    for e in entities
                    if e.get("type") == "equity" and e.get("symbol")
                ]

                item = NewsItem(
                    title=article.get("title", ""),
                    summary=article.get("description", "") or article.get("snippet", ""),
                    url=article.get("url", ""),
                    published_at=article.get("published_at", ""),
                    source=article.get("source", "unknown"),
                    sentiment_score=sentiment_score,
                    sentiment_label=self._score_to_label(sentiment_score),
                    tickers=tickers,
                    topics=[],  # Marketaux doesn't provide topics directly
                    provider=self.name,
                )
                result.append(item)

            self._set_cached(cache_key, [n.to_dict() for n in result])
            self.logger.info(f"Fetched {len(result)} news articles from Marketaux")
            return result

        except Exception as e:
            self.logger.warning(f"Failed to get news from Marketaux: {e}")
            return []

    def get_market_news(self, limit: int = 20) -> list[NewsItem]:
        """
        Get general market news (no symbol filter).

        Args:
            limit: Maximum number of articles

        Returns:
            List of NewsItem objects
        """
        return self.get_news(symbols=None, limit=limit)

    def search_news(
        self,
        keywords: list[str],
        limit: int = 20,
    ) -> list[NewsItem]:
        """
        Search news by keywords.

        Note: Marketaux doesn't have a direct search endpoint,
        so we fetch recent news and filter client-side.

        Args:
            keywords: Keywords to search for
            limit: Maximum results

        Returns:
            List of matching NewsItem objects
        """
        # Fetch more articles to filter from
        all_news = self.get_news(limit=50)

        # Filter by keywords
        keywords_lower = [k.lower() for k in keywords]
        matched = []

        for news in all_news:
            text = f"{news.title} {news.summary}".lower()
            if any(kw in text for kw in keywords_lower):
                matched.append(news)
                if len(matched) >= limit:
                    break

        return matched

    @staticmethod
    def _score_to_label(score: Optional[float]) -> Optional[str]:
        """Convert sentiment score to label."""
        if score is None:
            return None
        if score > 0.2:
            return "bullish"
        elif score < -0.2:
            return "bearish"
        return "neutral"
