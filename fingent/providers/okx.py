"""
OKX provider for cryptocurrency data.

Uses CCXT library for exchange integration.
Configured for demo trading by default.
"""

import time
from datetime import datetime, timedelta
from typing import Any, Optional

import ccxt

from fingent.core.errors import DataNotAvailableError, ProviderError
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.models import MarketData, PriceBar
from fingent.providers.base import BaseProvider, HealthCheckResult, ProviderStatus


class OKXProvider(BaseProvider):
    """
    OKX exchange provider for crypto data.

    Uses CCXT for unified exchange API.
    Supports both spot and futures markets.
    """

    name = "okx"

    # Common trading pairs
    DEFAULT_PAIRS = [
        "BTC/USDT",
        "ETH/USDT",
        "SOL/USDT",
    ]

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._exchange: Optional[ccxt.okx] = None

    def _initialize(self) -> None:
        """Initialize OKX client via CCXT."""
        config = {
            "apiKey": self.settings.okx_api_key,
            "secret": self.settings.okx_secret_key,
            "password": self.settings.okx_passphrase,
            "enableRateLimit": True,
        }

        # Demo trading mode
        if self.settings.okx_demo_trading:
            config["sandbox"] = True

        self._exchange = ccxt.okx(config)
        self.logger.info(
            f"OKX provider initialized (demo={self.settings.okx_demo_trading})"
        )

    @property
    def exchange(self) -> ccxt.okx:
        """Get OKX exchange client."""
        self._ensure_initialized()
        return self._exchange

    def healthcheck(self) -> HealthCheckResult:
        """Check OKX API health."""
        start_time = time.time()

        try:
            # Fetch ticker as health check
            self.exchange.fetch_ticker("BTC/USDT")
            latency = (time.time() - start_time) * 1000

            return HealthCheckResult(
                status=ProviderStatus.HEALTHY,
                message="OKX API is responding",
                latency_ms=latency,
            )
        except Exception as e:
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message=f"OKX API error: {e}",
            )

    def get_ticker(self, symbol: str) -> Optional[MarketData]:
        """
        Get current ticker for a trading pair.

        Args:
            symbol: Trading pair (e.g., "BTC/USDT" or "BTC-USDT")

        Returns:
            MarketData with current price info
        """
        # Normalize symbol format
        symbol = symbol.replace("-", "/")

        cache_key = f"ticker:{symbol}"
        cached = self._get_cached(cache_key)
        if cached:
            return MarketData.from_dict(cached)

        try:
            ticker = self.exchange.fetch_ticker(symbol)

            if not ticker:
                return None

            # Calculate 24h change
            change_24h = ticker.get("percentage")
            if change_24h is not None:
                change_24h = change_24h / 100  # Convert from percentage

            market_data = MarketData(
                symbol=symbol.replace("/", "-"),
                name=symbol.split("/")[0],  # BTC from BTC/USDT
                asset_type="crypto",
                price=ticker.get("last", 0),
                timestamp=format_timestamp(now_utc()),
                change_24h=change_24h,
                high_24h=ticker.get("high"),
                low_24h=ticker.get("low"),
                volume_24h=ticker.get("quoteVolume"),
                source=self.name,
            )

            self._set_cached(cache_key, market_data.to_dict())
            return market_data

        except Exception as e:
            self.logger.warning(f"Failed to get ticker for {symbol}: {e}")
            return None

    def get_tickers(
        self,
        symbols: Optional[list[str]] = None,
    ) -> dict[str, MarketData]:
        """
        Get tickers for multiple pairs.

        Args:
            symbols: List of trading pairs. Uses defaults if not provided.

        Returns:
            Dict mapping symbol to MarketData
        """
        if symbols is None:
            symbols = self.DEFAULT_PAIRS

        results = {}
        for symbol in symbols:
            ticker = self.get_ticker(symbol)
            if ticker:
                results[ticker.symbol] = ticker

        return results

    def get_ohlcv(
        self,
        symbol: str,
        timeframe: str = "1d",
        limit: int = 30,
    ) -> list[PriceBar]:
        """
        Get OHLCV candles.

        Args:
            symbol: Trading pair
            timeframe: Candle timeframe (1m, 5m, 15m, 1h, 4h, 1d, 1w)
            limit: Number of candles

        Returns:
            List of PriceBar objects
        """
        symbol = symbol.replace("-", "/")

        cache_key = f"ohlcv:{symbol}:{timeframe}:{limit}"
        cached = self._get_cached(cache_key)
        if cached:
            return [PriceBar.from_dict(c) for c in cached]

        try:
            ohlcv = self.exchange.fetch_ohlcv(symbol, timeframe, limit=limit)

            result = []
            for candle in ohlcv:
                bar = PriceBar(
                    symbol=symbol.replace("/", "-"),
                    timestamp=datetime.fromtimestamp(candle[0] / 1000).isoformat(),
                    open=candle[1],
                    high=candle[2],
                    low=candle[3],
                    close=candle[4],
                    volume=candle[5],
                    asset_type="crypto",
                )
                result.append(bar)

            self._set_cached(cache_key, [b.to_dict() for b in result])
            return result

        except Exception as e:
            self.logger.warning(f"Failed to get OHLCV for {symbol}: {e}")
            return []

    def calculate_price_changes(
        self,
        symbol: str,
    ) -> dict[str, Optional[float]]:
        """
        Calculate 24h and 7d price changes.

        Args:
            symbol: Trading pair

        Returns:
            Dict with change_24h and change_7d
        """
        try:
            # Get ticker for 24h change (already included)
            ticker = self.get_ticker(symbol)
            change_24h = ticker.change_24h if ticker else None

            # Get 7d change from OHLCV
            candles = self.get_ohlcv(symbol, timeframe="1d", limit=8)
            change_7d = None

            if len(candles) >= 7:
                current = candles[-1].close
                week_ago = candles[-7].close
                change_7d = (current - week_ago) / week_ago

            return {
                "change_24h": change_24h,
                "change_7d": change_7d,
            }

        except Exception as e:
            self.logger.warning(f"Failed to calculate changes for {symbol}: {e}")
            return {"change_24h": None, "change_7d": None}

    def get_crypto_snapshot(self) -> dict[str, Any]:
        """
        Get a snapshot of major crypto assets.

        Returns:
            Dict with prices and changes for major cryptos
        """
        tickers = self.get_tickers()

        snapshot = {
            "timestamp": format_timestamp(now_utc()),
            "assets": {},
        }

        for symbol, data in tickers.items():
            snapshot["assets"][symbol] = {
                "price": data.price,
                "change_24h": data.change_24h,
                "volume_24h": data.volume_24h,
            }

        # Add BTC dominance proxy (just price for now)
        btc = tickers.get("BTC-USDT")
        eth = tickers.get("ETH-USDT")

        if btc and eth:
            snapshot["btc_eth_ratio"] = btc.price / eth.price if eth.price > 0 else None

        return snapshot
