"""
Alpha Vantage provider for news sentiment.

Primary use: News sentiment analysis via NEWS_SENTIMENT API.
"""

import time
from datetime import datetime, timedelta
from typing import Any, Optional

from fingent.core.errors import DataNotAvailableError, ProviderError, RateLimitError
from fingent.core.http import HttpClient
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.models import MarketData, NewsItem
from fingent.providers.base import BaseProvider, HealthCheckResult, ProviderStatus


class AlphaVantageProvider(BaseProvider):
    """
    Alpha Vantage provider for news sentiment.

    Free tier: 25 requests/day
    Premium: Higher limits

    Primary endpoint: NEWS_SENTIMENT
    """

    name = "alphavantage"
    BASE_URL = "https://www.alphavantage.co/query"

    def _initialize(self) -> None:
        """Verify API key is configured."""
        if not self.settings.alphavantage_api_key:
            raise ProviderError(
                "Alpha Vantage API key not configured",
                provider=self.name,
                recoverable=False,
            )
        self.logger.info("Alpha Vantage provider initialized")

    def healthcheck(self) -> HealthCheckResult:
        """Check Alpha Vantage API health."""
        start_time = time.time()

        try:
            # Simple API call to check health
            params = {
                "function": "TIME_SERIES_INTRADAY",
                "symbol": "IBM",
                "interval": "5min",
                "apikey": self.settings.alphavantage_api_key,
            }
            response = self._make_request("get", self.BASE_URL, params=params)

            # Check for rate limit or error
            if "Note" in response or "Error Message" in response:
                return HealthCheckResult(
                    status=ProviderStatus.DEGRADED,
                    message=response.get("Note", response.get("Error Message", "Unknown error")),
                )

            latency = (time.time() - start_time) * 1000
            return HealthCheckResult(
                status=ProviderStatus.HEALTHY,
                message="Alpha Vantage API is responding",
                latency_ms=latency,
            )

        except Exception as e:
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message=f"Alpha Vantage API error: {e}",
            )

    def get_news_sentiment(
        self,
        tickers: Optional[list[str]] = None,
        topics: Optional[list[str]] = None,
        time_from: Optional[str] = None,
        limit: int = 50,
    ) -> list[NewsItem]:
        """
        Get news with sentiment analysis.

        Args:
            tickers: Filter by tickers (e.g., ["AAPL", "MSFT"])
            topics: Filter by topics (e.g., ["technology", "finance"])
            time_from: Start time (YYYYMMDDTHHMM format)
            limit: Max articles to return

        Returns:
            List of NewsItem with sentiment data
        """
        self._ensure_initialized()

        # Build cache key
        cache_key = f"news:{','.join(tickers or [])}:{','.join(topics or [])}:{time_from}"
        cached = self._get_cached(cache_key)
        if cached:
            return [NewsItem.from_dict(n) for n in cached]

        try:
            params = {
                "function": "NEWS_SENTIMENT",
                "apikey": self.settings.alphavantage_api_key,
                "limit": limit,
            }

            if tickers:
                params["tickers"] = ",".join(tickers)
            if topics:
                params["topics"] = ",".join(topics)
            if time_from:
                params["time_from"] = time_from

            response = self._make_request("get", self.BASE_URL, params=params)

            # Check for API limits/errors
            if "Note" in response:
                self.logger.warning(f"Alpha Vantage rate limit: {response['Note']}")
                raise RateLimitError(
                    response["Note"],
                    provider=self.name,
                )

            if "Error Message" in response:
                raise ProviderError(
                    response["Error Message"],
                    provider=self.name,
                )

            feed = response.get("feed", [])
            result = []

            for article in feed:
                # Extract overall sentiment
                sentiment_score = article.get("overall_sentiment_score", 0)
                sentiment_label = article.get("overall_sentiment_label", "Neutral")

                # Map to our labels
                if sentiment_label in ["Bullish", "Somewhat-Bullish"]:
                    normalized_label = "bullish"
                elif sentiment_label in ["Bearish", "Somewhat-Bearish"]:
                    normalized_label = "bearish"
                else:
                    normalized_label = "neutral"

                # Extract ticker relevance
                ticker_sentiment = article.get("ticker_sentiment", [])
                article_tickers = [t.get("ticker", "") for t in ticker_sentiment]

                # Extract topics
                article_topics = [
                    topic.get("topic", "")
                    for topic in article.get("topics", [])
                ]

                item = NewsItem(
                    title=article.get("title", ""),
                    summary=article.get("summary", ""),
                    url=article.get("url", ""),
                    published_at=self._parse_timestamp(
                        article.get("time_published", "")
                    ),
                    source=article.get("source", "unknown"),
                    sentiment_label=normalized_label,
                    sentiment_score=sentiment_score,
                    tickers=article_tickers,
                    topics=article_topics,
                    provider=self.name,
                )
                result.append(item)

            self._set_cached(cache_key, [n.to_dict() for n in result])
            return result

        except (RateLimitError, ProviderError):
            raise
        except Exception as e:
            self.logger.error(f"Failed to get news sentiment: {e}")
            raise DataNotAvailableError(
                f"News sentiment unavailable: {e}",
                provider=self.name,
            ) from e

    def get_market_sentiment_summary(
        self,
        tickers: Optional[list[str]] = None,
    ) -> dict[str, Any]:
        """
        Get aggregated sentiment summary.

        Args:
            tickers: Tickers to analyze

        Returns:
            Summary with average sentiment and distribution
        """
        try:
            news = self.get_news_sentiment(tickers=tickers, limit=50)

            if not news:
                return {
                    "article_count": 0,
                    "avg_sentiment": 0,
                    "sentiment_distribution": {},
                }

            # Calculate averages
            scores = [n.sentiment_score for n in news if n.sentiment_score is not None]
            avg_score = sum(scores) / len(scores) if scores else 0

            # Distribution
            distribution = {"bullish": 0, "neutral": 0, "bearish": 0}
            for item in news:
                label = item.sentiment_label or "neutral"
                distribution[label] = distribution.get(label, 0) + 1

            return {
                "article_count": len(news),
                "avg_sentiment": round(avg_score, 3),
                "sentiment_distribution": distribution,
                "latest_articles": [n.to_dict() for n in news[:5]],
            }

        except Exception as e:
            self.logger.warning(f"Failed to get sentiment summary: {e}")
            return {
                "article_count": 0,
                "avg_sentiment": 0,
                "sentiment_distribution": {},
                "error": str(e),
            }

    def get_quote(self, symbol: str) -> Optional[MarketData]:
        """
        Get current quote (backup method, Finnhub preferred).

        Args:
            symbol: Stock symbol

        Returns:
            MarketData or None
        """
        self._ensure_initialized()

        cache_key = f"quote:{symbol}"
        cached = self._get_cached(cache_key)
        if cached:
            return MarketData.from_dict(cached)

        try:
            params = {
                "function": "GLOBAL_QUOTE",
                "symbol": symbol,
                "apikey": self.settings.alphavantage_api_key,
            }

            response = self._make_request("get", self.BASE_URL, params=params)

            if "Note" in response:
                raise RateLimitError(response["Note"], provider=self.name)

            quote = response.get("Global Quote", {})
            if not quote:
                return None

            price = float(quote.get("05. price", 0))
            prev_close = float(quote.get("08. previous close", 0))
            change_24h = (price - prev_close) / prev_close if prev_close > 0 else None

            market_data = MarketData(
                symbol=symbol,
                name=symbol,
                asset_type="us_equity",
                price=price,
                timestamp=format_timestamp(now_utc()),
                change_24h=change_24h,
                high_24h=float(quote.get("03. high", 0)) or None,
                low_24h=float(quote.get("04. low", 0)) or None,
                volume_24h=float(quote.get("06. volume", 0)) or None,
                source=self.name,
            )

            self._set_cached(cache_key, market_data.to_dict())
            return market_data

        except (RateLimitError, ProviderError):
            raise
        except Exception as e:
            self.logger.warning(f"Failed to get quote for {symbol}: {e}")
            return None

    def _parse_timestamp(self, timestamp_str: str) -> str:
        """Parse Alpha Vantage timestamp format."""
        try:
            # Format: 20240115T120000
            dt = datetime.strptime(timestamp_str, "%Y%m%dT%H%M%S")
            return dt.isoformat()
        except Exception:
            return timestamp_str
