"""
Finnhub provider for market data and news.

Provides:
- Real-time and historical quotes
- Company news
- Market sentiment
"""

import time
from datetime import datetime, timedelta
from typing import Any, Optional

import finnhub

from fingent.core.errors import DataNotAvailableError, ProviderError
from fingent.core.timeutil import format_timestamp, now_utc, days_ago
from fingent.domain.models import MarketData, NewsItem, PriceBar
from fingent.providers.base import BaseProvider, HealthCheckResult, ProviderStatus


class FinnhubProvider(BaseProvider):
    """
    Finnhub data provider for quotes and news.

    Free tier limitations:
    - 60 API calls/minute
    - Limited historical data
    """

    name = "finnhub"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client: Optional[finnhub.Client] = None

    def _initialize(self) -> None:
        """Initialize Finnhub client."""
        api_key = self.settings.finnhub_api_key
        if not api_key:
            raise ProviderError(
                "Finnhub API key not configured",
                provider=self.name,
                recoverable=False,
            )
        self._client = finnhub.Client(api_key=api_key)
        self.logger.info("Finnhub provider initialized")

    @property
    def client(self) -> finnhub.Client:
        """Get Finnhub client, initializing if needed."""
        self._ensure_initialized()
        return self._client

    def healthcheck(self) -> HealthCheckResult:
        """Check Finnhub API health."""
        start_time = time.time()

        try:
            # Simple quote request
            self.client.quote("AAPL")
            latency = (time.time() - start_time) * 1000

            return HealthCheckResult(
                status=ProviderStatus.HEALTHY,
                message="Finnhub API is responding",
                latency_ms=latency,
            )
        except Exception as e:
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message=f"Finnhub API error: {e}",
            )

    def get_quote(self, symbol: str) -> Optional[MarketData]:
        """
        Get current quote for a symbol.

        Args:
            symbol: Stock symbol (e.g., "AAPL", "SPY")

        Returns:
            MarketData with current price info
        """
        cache_key = f"quote:{symbol}"
        cached = self._get_cached(cache_key)
        if cached:
            return MarketData.from_dict(cached)

        try:
            quote = self.client.quote(symbol)

            if not quote or quote.get("c", 0) == 0:
                return None

            # Calculate 24h change
            change_24h = None
            if quote.get("pc", 0) > 0:
                change_24h = (quote["c"] - quote["pc"]) / quote["pc"]

            market_data = MarketData(
                symbol=symbol,
                name=symbol,  # Finnhub doesn't return name in quote
                asset_type="us_equity",
                price=quote["c"],
                timestamp=format_timestamp(now_utc()),
                change_24h=change_24h,
                high_24h=quote.get("h"),
                low_24h=quote.get("l"),
                source=self.name,
            )

            self._set_cached(cache_key, market_data.to_dict())
            return market_data

        except Exception as e:
            self.logger.warning(f"Failed to get quote for {symbol}: {e}")
            return None

    def get_quotes(self, symbols: list[str]) -> dict[str, MarketData]:
        """
        Get quotes for multiple symbols.

        Args:
            symbols: List of stock symbols

        Returns:
            Dict mapping symbol to MarketData
        """
        results = {}
        for symbol in symbols:
            quote = self.get_quote(symbol)
            if quote:
                results[symbol] = quote
        return results

    def get_candles(
        self,
        symbol: str,
        resolution: str = "D",
        from_timestamp: Optional[int] = None,
        to_timestamp: Optional[int] = None,
    ) -> list[PriceBar]:
        """
        Get historical candles/OHLCV data.

        Args:
            symbol: Stock symbol
            resolution: Candle resolution (1, 5, 15, 30, 60, D, W, M)
            from_timestamp: Start timestamp (Unix)
            to_timestamp: End timestamp (Unix)

        Returns:
            List of PriceBar objects
        """
        if to_timestamp is None:
            to_timestamp = int(datetime.now().timestamp())
        if from_timestamp is None:
            from_timestamp = int((datetime.now() - timedelta(days=30)).timestamp())

        cache_key = f"candles:{symbol}:{resolution}:{from_timestamp}:{to_timestamp}"
        cached = self._get_cached(cache_key)
        if cached:
            return [PriceBar.from_dict(c) for c in cached]

        try:
            candles = self.client.stock_candles(
                symbol,
                resolution,
                from_timestamp,
                to_timestamp,
            )

            if candles.get("s") != "ok" or not candles.get("t"):
                return []

            result = []
            for i in range(len(candles["t"])):
                bar = PriceBar(
                    symbol=symbol,
                    timestamp=datetime.fromtimestamp(candles["t"][i]).isoformat(),
                    open=candles["o"][i],
                    high=candles["h"][i],
                    low=candles["l"][i],
                    close=candles["c"][i],
                    volume=candles["v"][i] if "v" in candles else None,
                    asset_type="us_equity",
                )
                result.append(bar)

            self._set_cached(cache_key, [b.to_dict() for b in result])
            return result

        except Exception as e:
            self.logger.warning(f"Failed to get candles for {symbol}: {e}")
            return []

    def get_company_news(
        self,
        symbol: str,
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
    ) -> list[NewsItem]:
        """
        Get company news.

        Args:
            symbol: Stock symbol
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)

        Returns:
            List of NewsItem objects
        """
        if to_date is None:
            to_date = datetime.now().strftime("%Y-%m-%d")
        if from_date is None:
            from_date = (datetime.now() - timedelta(days=7)).strftime("%Y-%m-%d")

        cache_key = f"news:{symbol}:{from_date}:{to_date}"
        cached = self._get_cached(cache_key)
        if cached:
            return [NewsItem.from_dict(n) for n in cached]

        try:
            news = self.client.company_news(symbol, from_date, to_date)

            result = []
            for article in news[:20]:  # Limit to 20 articles
                item = NewsItem(
                    title=article.get("headline", ""),
                    summary=article.get("summary", ""),
                    url=article.get("url", ""),
                    published_at=datetime.fromtimestamp(
                        article.get("datetime", 0)
                    ).isoformat(),
                    source=article.get("source", "unknown"),
                    tickers=[symbol],
                    provider=self.name,
                )
                result.append(item)

            self._set_cached(cache_key, [n.to_dict() for n in result])
            return result

        except Exception as e:
            self.logger.warning(f"Failed to get news for {symbol}: {e}")
            return []

    def get_market_news(self, category: str = "general") -> list[NewsItem]:
        """
        Get general market news.

        Args:
            category: News category (general, forex, crypto, merger)

        Returns:
            List of NewsItem objects
        """
        cache_key = f"market_news:{category}"
        cached = self._get_cached(cache_key)
        if cached:
            return [NewsItem.from_dict(n) for n in cached]

        try:
            news = self.client.general_news(category)

            result = []
            for article in news[:20]:
                item = NewsItem(
                    title=article.get("headline", ""),
                    summary=article.get("summary", ""),
                    url=article.get("url", ""),
                    published_at=datetime.fromtimestamp(
                        article.get("datetime", 0)
                    ).isoformat(),
                    source=article.get("source", "unknown"),
                    tickers=[],
                    provider=self.name,
                )
                result.append(item)

            self._set_cached(cache_key, [n.to_dict() for n in result])
            return result

        except Exception as e:
            self.logger.warning(f"Failed to get market news: {e}")
            return []

    def calculate_price_changes(
        self,
        symbol: str,
    ) -> dict[str, Optional[float]]:
        """
        Calculate 24h and 7d price changes.

        Uses quote data instead of candles (candles require premium subscription).

        Args:
            symbol: Stock symbol

        Returns:
            Dict with change_24h and change_7d
        """
        try:
            # 使用 quote 数据获取 24h 变化（免费 API 支持）
            quote = self.get_quote(symbol)

            if quote and quote.change_24h is not None:
                return {
                    "change_24h": quote.change_24h,
                    "change_7d": None,  # Quote 数据不包含 7d 变化
                }

            return {"change_24h": None, "change_7d": None}

        except Exception as e:
            self.logger.warning(f"Failed to calculate changes for {symbol}: {e}")
            return {"change_24h": None, "change_7d": None}
