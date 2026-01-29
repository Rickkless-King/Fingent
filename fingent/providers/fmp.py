"""
Financial Modeling Prep (FMP) provider for financial news.

Provides:
- General market news
- Stock-specific news
- Press releases
- Crypto/Forex news

Free tier: 500MB bandwidth/month (~5000-10000 requests)
Docs: https://site.financialmodelingprep.com/developer/docs
"""

import time
from datetime import datetime, timedelta
from typing import Any, Optional

from fingent.core.errors import ProviderError
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.models import NewsItem
from fingent.providers.base import BaseProvider, HealthCheckResult, ProviderStatus


class FMPProvider(BaseProvider):
    """
    Financial Modeling Prep news provider.

    Professional financial data with news, press releases.

    Free tier limitations:
    - 500MB bandwidth/month
    - Access to most endpoints
    """

    name = "fmp"
    BASE_URL = "https://financialmodelingprep.com"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._api_key: Optional[str] = None

    def _initialize(self) -> None:
        """Initialize FMP client."""
        self._api_key = self.settings.fmp_api_key
        if not self._api_key:
            self.logger.warning("FMP API key not configured")
        else:
            self.logger.info("FMP provider initialized")

    @property
    def is_configured(self) -> bool:
        """Check if API key is configured."""
        self._ensure_initialized()
        return bool(self._api_key)

    def healthcheck(self) -> HealthCheckResult:
        """Check FMP API health."""
        if not self.is_configured:
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message="FMP API key not configured",
            )

        start_time = time.time()

        try:
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/api/v3/stock/list",
                params={"apikey": self._api_key},
            )

            latency = (time.time() - start_time) * 1000

            return HealthCheckResult(
                status=ProviderStatus.HEALTHY,
                message="FMP API is responding",
                latency_ms=latency,
            )

        except Exception as e:
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message=f"FMP API error: {e}",
            )

    def get_general_news(
        self,
        limit: int = 20,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> list[NewsItem]:
        """
        Get general market news.

        Args:
            limit: Maximum number of articles (max 250)
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)

        Returns:
            List of NewsItem objects
        """
        if not self.is_configured:
            self.logger.warning("FMP not configured, returning empty")
            return []

        cache_key = f"general_news:{limit}:{from_date}:{to_date}"
        cached = self._get_cached(cache_key)
        if cached:
            return [NewsItem.from_dict(n) for n in cached]

        try:
            # Use the stable endpoint
            params = {
                "apikey": self._api_key,
                "page": 0,
                "limit": min(limit, 250),
            }

            if from_date:
                params["from"] = from_date
            if to_date:
                params["to"] = to_date

            self._consume_quota()
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/stable/news/general-latest",
                params=params,
            )

            # FMP returns a list directly
            articles = response if isinstance(response, list) else []
            result = []

            for article in articles:
                item = NewsItem(
                    title=article.get("title", "") or article.get("headline", ""),
                    summary=article.get("text", "") or article.get("snippet", ""),
                    url=article.get("url", "") or article.get("link", ""),
                    published_at=self._parse_fmp_date(article.get("publishedDate", "")),
                    source=article.get("site", "") or article.get("source", "FMP"),
                    sentiment_score=None,  # FMP doesn't provide sentiment
                    sentiment_label=None,
                    tickers=[],
                    topics=[],
                    provider=self.name,
                )
                result.append(item)

            self._set_cached(cache_key, [n.to_dict() for n in result])
            self.logger.info(f"Fetched {len(result)} news articles from FMP")
            return result

        except Exception as e:
            self.logger.warning(f"Failed to get news from FMP: {e}")
            # Try legacy endpoint as fallback
            return self._get_news_legacy(limit)

    def _get_news_legacy(self, limit: int = 20) -> list[NewsItem]:
        """Fallback to legacy news endpoint."""
        try:
            self._consume_quota()
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/api/v3/fmp/articles",
                params={
                    "apikey": self._api_key,
                    "page": 0,
                    "size": min(limit, 50),
                },
            )

            articles = response.get("content", []) if isinstance(response, dict) else []
            result = []

            for article in articles:
                item = NewsItem(
                    title=article.get("title", ""),
                    summary=article.get("content", "")[:500] if article.get("content") else "",
                    url=article.get("link", ""),
                    published_at=article.get("date", ""),
                    source="FMP",
                    sentiment_score=None,
                    sentiment_label=None,
                    tickers=article.get("tickers", "").split(",") if article.get("tickers") else [],
                    topics=[],
                    provider=self.name,
                )
                result.append(item)

            return result

        except Exception as e:
            self.logger.warning(f"FMP legacy endpoint also failed: {e}")
            return []

    def get_stock_news(
        self,
        symbol: str,
        limit: int = 20,
    ) -> list[NewsItem]:
        """
        Get news for a specific stock.

        Args:
            symbol: Stock ticker (e.g., "AAPL")
            limit: Maximum number of articles

        Returns:
            List of NewsItem objects
        """
        if not self.is_configured:
            return []

        cache_key = f"stock_news:{symbol}:{limit}"
        cached = self._get_cached(cache_key)
        if cached:
            return [NewsItem.from_dict(n) for n in cached]

        try:
            self._consume_quota()
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/api/v3/stock_news",
                params={
                    "apikey": self._api_key,
                    "tickers": symbol,
                    "limit": min(limit, 100),
                },
            )

            articles = response if isinstance(response, list) else []
            result = []

            for article in articles:
                item = NewsItem(
                    title=article.get("title", ""),
                    summary=article.get("text", ""),
                    url=article.get("url", ""),
                    published_at=self._parse_fmp_date(article.get("publishedDate", "")),
                    source=article.get("site", "unknown"),
                    sentiment_score=None,
                    sentiment_label=None,
                    tickers=[article.get("symbol", symbol)],
                    topics=[],
                    provider=self.name,
                )
                result.append(item)

            self._set_cached(cache_key, [n.to_dict() for n in result])
            return result

        except Exception as e:
            self.logger.warning(f"Failed to get stock news from FMP: {e}")
            return []

    def get_market_news(self, limit: int = 20) -> list[NewsItem]:
        """Alias for get_general_news for consistency."""
        return self.get_general_news(limit=limit)

    def search_news(
        self,
        keywords: list[str],
        limit: int = 20,
    ) -> list[NewsItem]:
        """
        Search news by keywords.

        FMP doesn't have direct keyword search, so we filter client-side.

        Args:
            keywords: Keywords to search for
            limit: Maximum results

        Returns:
            List of matching NewsItem objects
        """
        all_news = self.get_general_news(limit=100)

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
    def _parse_fmp_date(date_str: str) -> str:
        """Parse FMP date format to ISO format."""
        if not date_str:
            return ""

        try:
            # FMP uses format like "2024-01-15 10:30:00"
            if " " in date_str:
                dt = datetime.strptime(date_str, "%Y-%m-%d %H:%M:%S")
            else:
                dt = datetime.strptime(date_str, "%Y-%m-%d")
            return dt.isoformat()
        except ValueError:
            return date_str
