"""
Polymarket provider for prediction market data.

This is an OPTIONAL provider that gracefully degrades if unavailable.
Used for sentiment indicators like Fed rate probabilities.
"""

import time
from typing import Any, Optional

from fingent.core.errors import DataNotAvailableError, ProviderError
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.models import SentimentData
from fingent.providers.base import (
    OptionalProvider,
    HealthCheckResult,
    ProviderStatus,
)


class PolymarketProvider(OptionalProvider):
    """
    Polymarket prediction market provider.

    OPTIONAL: Fails gracefully if unavailable.

    Provides:
    - Fed rate probability markets
    - Economic event markets
    - Political event markets
    """

    name = "polymarket"

    # Known market IDs for common questions
    # These need to be updated as markets change
    KNOWN_MARKETS = {
        "fed_march_rate": {
            "id": "placeholder",  # Update with real market ID
            "question": "Will Fed raise rates in March 2026?",
        },
        "recession_2026": {
            "id": "placeholder",
            "question": "Will there be a US recession in 2026?",
        },
    }

    BASE_URL = "https://gamma-api.polymarket.com"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        # Check if enabled in settings
        if not self.settings.polymarket_enabled:
            self._enabled = False

    def _initialize(self) -> None:
        """Initialize Polymarket client."""
        if not self.settings.polymarket_api_key:
            self.logger.warning("Polymarket API key not configured, disabling provider")
            self._enabled = False
            return

        self.logger.info("Polymarket provider initialized (Optional)")

    def healthcheck(self) -> HealthCheckResult:
        """Check Polymarket API health."""
        if not self.is_enabled:
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message="Polymarket provider is disabled",
            )

        start_time = time.time()

        try:
            # Try to fetch markets list
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/markets",
                params={"limit": 1},
            )

            latency = (time.time() - start_time) * 1000

            return HealthCheckResult(
                status=ProviderStatus.HEALTHY,
                message="Polymarket API is responding",
                latency_ms=latency,
            )

        except Exception as e:
            self.logger.warning(f"Polymarket health check failed: {e}")
            return HealthCheckResult(
                status=ProviderStatus.UNAVAILABLE,
                message=f"Polymarket API error: {e}",
            )

    def get_market(self, market_id: str) -> Optional[SentimentData]:
        """
        Get data for a specific market.

        Args:
            market_id: Polymarket market ID

        Returns:
            SentimentData or None if unavailable
        """
        if not self.is_enabled:
            return None

        cache_key = f"market:{market_id}"
        cached = self._get_cached(cache_key)
        if cached:
            return SentimentData.from_dict(cached)

        try:
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/markets/{market_id}",
            )

            if not response:
                return None

            data = SentimentData(
                source=self.name,
                market_id=market_id,
                question=response.get("question", ""),
                yes_price=float(response.get("outcomePrices", [0, 0])[0]),
                no_price=float(response.get("outcomePrices", [0, 0])[1]),
                volume=float(response.get("volume", 0)),
                timestamp=format_timestamp(now_utc()),
            )

            self._set_cached(cache_key, data.to_dict())
            return data

        except Exception as e:
            self.logger.warning(f"Failed to get market {market_id}: {e}")
            return None

    def search_markets(
        self,
        query: str,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        """
        Search for markets by keyword.

        Args:
            query: Search query
            limit: Max results

        Returns:
            List of market dicts
        """
        if not self.is_enabled:
            return []

        try:
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/markets",
                params={
                    "tag_slug": query.lower(),
                    "limit": limit,
                },
            )

            return response if isinstance(response, list) else []

        except Exception as e:
            self.logger.warning(f"Failed to search markets: {e}")
            return []

    def get_fed_sentiment(self) -> Optional[dict[str, Any]]:
        """
        Get Fed-related market sentiment.

        Returns aggregated data from Fed-related prediction markets.

        Returns:
            Dict with Fed sentiment data or None
        """
        if not self.is_enabled:
            return None

        try:
            # Search for Fed-related markets
            markets = self.search_markets("federal reserve", limit=5)

            if not markets:
                return None

            result = {
                "timestamp": format_timestamp(now_utc()),
                "markets": [],
                "avg_hawkish_probability": 0,
            }

            hawkish_probs = []

            for market in markets:
                market_data = {
                    "question": market.get("question", ""),
                    "yes_price": market.get("outcomePrices", [0])[0],
                    "volume": market.get("volume", 0),
                }
                result["markets"].append(market_data)

                # Estimate hawkish probability based on market type
                # This is simplified - real implementation would need market-specific logic
                if "rate" in market.get("question", "").lower():
                    if "raise" in market.get("question", "").lower():
                        hawkish_probs.append(market_data["yes_price"])
                    elif "cut" in market.get("question", "").lower():
                        hawkish_probs.append(1 - market_data["yes_price"])

            if hawkish_probs:
                result["avg_hawkish_probability"] = sum(hawkish_probs) / len(hawkish_probs)

            return result

        except Exception as e:
            self.logger.warning(f"Failed to get Fed sentiment: {e}")
            return None

    def safe_get_sentiment(self) -> dict[str, Any]:
        """
        Safely get sentiment data with fallback.

        This method NEVER raises exceptions.

        Returns:
            Sentiment data or empty dict
        """
        return self.safe_fetch(
            self.get_fed_sentiment,
            default={
                "available": False,
                "reason": "Provider disabled or unavailable",
            },
        ) or {
            "available": False,
            "reason": "Provider disabled or unavailable",
        }
