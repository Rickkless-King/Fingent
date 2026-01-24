"""
Polygon.io (Massive) provider for US stock market data.

Provides:
- Real-time and historical stock quotes
- OHLCV (candlestick) data
- Market-wide data and indices
- Pre/post market data

Note: Polygon.io was rebranded to Massive in late 2025.
Both names refer to the same service.

Reference: https://polygon.io/ / https://massive.com/
"""

import time
from datetime import datetime, timedelta
from typing import Any, Optional

from fingent.core.errors import DataNotAvailableError, ProviderError
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.models import MarketData, PriceBar
from fingent.providers.base import BaseProvider, HealthCheckResult, ProviderStatus


class PolygonProvider(BaseProvider):
    """
    Polygon.io (Massive) data provider for US equities.

    Features:
    - Real-time quotes (with paid plan)
    - 2+ years of historical data (free tier)
    - Pre/post market data
    - Options and crypto data

    Free tier limitations:
    - 5 API calls/minute
    - End-of-day data only
    - 2 years historical data
    """

    name = "polygon"

    # Common tickers for reference
    MAJOR_TICKERS = {
        # Indices (via ETFs)
        "SPY": "S&P 500 ETF",
        "QQQ": "Nasdaq 100 ETF",
        "DIA": "Dow Jones ETF",
        "IWM": "Russell 2000 ETF",
        "VIX": "Volatility Index",  # Note: VIX needs special handling

        # Major stocks
        "AAPL": "Apple",
        "MSFT": "Microsoft",
        "GOOGL": "Google",
        "AMZN": "Amazon",
        "NVDA": "NVIDIA",
        "TSLA": "Tesla",
        "META": "Meta",

        # Sector ETFs
        "XLF": "Financials ETF",
        "XLE": "Energy ETF",
        "XLK": "Technology ETF",
        "XLV": "Healthcare ETF",

        # Bond ETFs
        "TLT": "20+ Year Treasury ETF",
        "SHY": "1-3 Year Treasury ETF",
        "LQD": "Investment Grade Corp Bond ETF",

        # Commodities
        "GLD": "Gold ETF",
        "SLV": "Silver ETF",
        "USO": "Oil ETF",
    }

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._client = None

    def _initialize(self) -> None:
        """Initialize Polygon client."""
        api_key = self.settings.polygon_api_key
        if not api_key:
            raise ProviderError(
                "Polygon API key not configured. Get one at https://polygon.io/",
                provider=self.name,
                recoverable=False,
            )

        try:
            from polygon import StocksClient
            self._client = StocksClient(api_key)
            self.logger.info("Polygon provider initialized")
        except ImportError as e:
            raise ProviderError(
                "polygon package not installed. Run: pip install polygon",
                provider=self.name,
                recoverable=False,
            ) from e

    @property
    def client(self):
        """Get Polygon client, initializing if needed."""
        self._ensure_initialized()
        return self._client

    def healthcheck(self) -> HealthCheckResult:
        """Check Polygon API health."""
        start_time = time.time()

        try:
            # Simple request to check API health
            result = self.client.get_previous_close("AAPL")
            latency = (time.time() - start_time) * 1000

            if result:
                return HealthCheckResult(
                    status=ProviderStatus.HEALTHY,
                    message="Polygon API is responding",
                    latency_ms=latency,
                )
            else:
                return HealthCheckResult(
                    status=ProviderStatus.DEGRADED,
                    message="Polygon API returned empty response",
                )

        except Exception as e:
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message=f"Polygon API error: {e}",
            )

    def get_quote(self, symbol: str) -> Optional[MarketData]:
        """
        Get current/latest quote for a symbol.

        Args:
            symbol: Stock ticker symbol (e.g., "AAPL", "SPY")

        Returns:
            MarketData with price info
        """
        cache_key = f"quote:{symbol}"
        cached = self._get_cached(cache_key)
        if cached:
            return MarketData.from_dict(cached)

        try:
            # Get previous close (most recent complete bar)
            result = self.client.get_previous_close(symbol)

            if not result or len(result) == 0:
                return None

            # polygon returns a list, get first item
            data = result[0] if isinstance(result, list) else result

            # Handle different response formats
            if hasattr(data, 'close'):
                close_price = data.close
                open_price = data.open
                high_price = data.high
                low_price = data.low
                volume = data.volume
                timestamp = data.timestamp if hasattr(data, 'timestamp') else None
            else:
                close_price = data.get('c') or data.get('close')
                open_price = data.get('o') or data.get('open')
                high_price = data.get('h') or data.get('high')
                low_price = data.get('l') or data.get('low')
                volume = data.get('v') or data.get('volume')
                timestamp = data.get('t') or data.get('timestamp')

            if not close_price:
                return None

            # Calculate 24h change if we have open
            change_24h = None
            if open_price and open_price > 0:
                change_24h = (close_price - open_price) / open_price

            market_data = MarketData(
                symbol=symbol,
                name=self.MAJOR_TICKERS.get(symbol, symbol),
                asset_type="us_equity",
                price=float(close_price),
                timestamp=format_timestamp(now_utc()),
                change_24h=change_24h,
                high_24h=float(high_price) if high_price else None,
                low_24h=float(low_price) if low_price else None,
                volume_24h=float(volume) if volume else None,
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

    def get_bars(
        self,
        symbol: str,
        timespan: str = "day",
        from_date: Optional[str] = None,
        to_date: Optional[str] = None,
        limit: int = 30,
    ) -> list[PriceBar]:
        """
        Get historical OHLCV bars.

        Args:
            symbol: Stock symbol
            timespan: Bar timespan (minute, hour, day, week, month)
            from_date: Start date (YYYY-MM-DD)
            to_date: End date (YYYY-MM-DD)
            limit: Max bars to return

        Returns:
            List of PriceBar objects
        """
        if to_date is None:
            to_date = datetime.now().strftime("%Y-%m-%d")
        if from_date is None:
            # Default to 30 days ago
            from_date = (datetime.now() - timedelta(days=30)).strftime("%Y-%m-%d")

        cache_key = f"bars:{symbol}:{timespan}:{from_date}:{to_date}:{limit}"
        cached = self._get_cached(cache_key)
        if cached:
            return [PriceBar.from_dict(b) for b in cached]

        try:
            # Use get_aggregate_bars for historical data
            bars = self.client.get_aggregate_bars(
                symbol,
                from_date,
                to_date,
                timespan=timespan,
                limit=limit,
            )

            if not bars:
                return []

            result = []
            for bar in bars:
                # Handle different response formats
                if hasattr(bar, 'open'):
                    price_bar = PriceBar(
                        symbol=symbol,
                        timestamp=datetime.fromtimestamp(
                            bar.timestamp / 1000 if bar.timestamp > 1e12 else bar.timestamp
                        ).isoformat(),
                        open=float(bar.open),
                        high=float(bar.high),
                        low=float(bar.low),
                        close=float(bar.close),
                        volume=float(bar.volume) if hasattr(bar, 'volume') else None,
                        asset_type="us_equity",
                    )
                else:
                    price_bar = PriceBar(
                        symbol=symbol,
                        timestamp=datetime.fromtimestamp(
                            bar.get('t', 0) / 1000 if bar.get('t', 0) > 1e12 else bar.get('t', 0)
                        ).isoformat(),
                        open=float(bar.get('o', 0)),
                        high=float(bar.get('h', 0)),
                        low=float(bar.get('l', 0)),
                        close=float(bar.get('c', 0)),
                        volume=float(bar.get('v', 0)) if 'v' in bar else None,
                        asset_type="us_equity",
                    )
                result.append(price_bar)

            self._set_cached(cache_key, [b.to_dict() for b in result])
            return result

        except Exception as e:
            self.logger.warning(f"Failed to get bars for {symbol}: {e}")
            return []

    def calculate_price_changes(
        self,
        symbol: str,
    ) -> dict[str, Optional[float]]:
        """
        Calculate 24h and 7d price changes.

        Args:
            symbol: Stock symbol

        Returns:
            Dict with change_24h and change_7d
        """
        try:
            bars = self.get_bars(
                symbol,
                timespan="day",
                from_date=(datetime.now() - timedelta(days=10)).strftime("%Y-%m-%d"),
            )

            if not bars:
                return {"change_24h": None, "change_7d": None}

            current = bars[-1].close

            # 24h change (previous close)
            change_24h = None
            if len(bars) >= 2:
                prev = bars[-2].close
                change_24h = (current - prev) / prev

            # 7d change
            change_7d = None
            if len(bars) >= 7:
                week_ago = bars[-7].close
                change_7d = (current - week_ago) / week_ago

            return {
                "change_24h": change_24h,
                "change_7d": change_7d,
            }

        except Exception as e:
            self.logger.warning(f"Failed to calculate changes for {symbol}: {e}")
            return {"change_24h": None, "change_7d": None}

    def get_market_snapshot(
        self,
        symbols: Optional[list[str]] = None,
    ) -> dict[str, MarketData]:
        """
        Get a snapshot of multiple market tickers.

        Args:
            symbols: List of symbols. Uses major indices if not provided.

        Returns:
            Dict mapping symbol to MarketData
        """
        if symbols is None:
            # Default to major indices and key stocks
            symbols = ["SPY", "QQQ", "DIA", "IWM", "AAPL", "MSFT", "NVDA"]

        return self.get_quotes(symbols)

    def get_sector_performance(self) -> dict[str, MarketData]:
        """
        Get performance of major sector ETFs.

        Returns:
            Dict mapping sector to MarketData
        """
        sector_etfs = {
            "Technology": "XLK",
            "Financials": "XLF",
            "Energy": "XLE",
            "Healthcare": "XLV",
            "Consumer Discretionary": "XLY",
            "Consumer Staples": "XLP",
            "Industrials": "XLI",
            "Materials": "XLB",
            "Utilities": "XLU",
            "Real Estate": "XLRE",
            "Communication": "XLC",
        }

        results = {}
        for sector, etf in sector_etfs.items():
            quote = self.get_quote(etf)
            if quote:
                quote.name = f"{sector} ({etf})"
                results[sector] = quote

        return results

    def get_treasury_yields(self) -> dict[str, MarketData]:
        """
        Get treasury yield proxies via ETFs.

        Returns:
            Dict mapping duration to MarketData
        """
        treasury_etfs = {
            "short_term": "SHY",   # 1-3 Year
            "intermediate": "IEF",  # 7-10 Year
            "long_term": "TLT",    # 20+ Year
        }

        results = {}
        for duration, etf in treasury_etfs.items():
            quote = self.get_quote(etf)
            if quote:
                results[duration] = quote

        return results
