"""
Polymarket provider for prediction market data.

This is an OPTIONAL provider that gracefully degrades if unavailable.
Used for sentiment indicators like Fed rate probabilities.

Supports:
- Gamma API: Market/Event metadata, search
- CLOB API: Orderbook, quotes for arbitrage detection
"""

import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

from fingent.core.errors import DataNotAvailableError, ProviderError
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.models import (
    SentimentData,
    PolymarketEvent,
    PolymarketMarket,
    PolymarketQuote,
)
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

    # ==============================================
    # Arbitrage Support: Events, Markets, CLOB
    # ==============================================

    CLOB_BASE_URL = "https://clob.polymarket.com"

    def get_events(
        self,
        tag: Optional[str] = None,
        active: bool = True,
        limit: int = 50,
    ) -> list[PolymarketEvent]:
        """
        Get events from Gamma API.

        Args:
            tag: Filter by tag (e.g., "fed", "ai", "politics")
            active: Only return active events
            limit: Max results

        Returns:
            List of PolymarketEvent objects
        """
        if not self.is_enabled:
            return []

        cache_key = f"events:{tag or 'all'}:{active}:{limit}"
        cached = self._get_cached(cache_key)
        if cached:
            return [PolymarketEvent.from_dict(e) for e in cached]

        try:
            params = {"limit": limit, "active": str(active).lower()}
            if tag:
                params["tag"] = tag

            response = self._make_request(
                "get",
                f"{self.BASE_URL}/events",
                params=params,
            )

            events = []
            for item in (response if isinstance(response, list) else []):
                event = PolymarketEvent(
                    event_id=item.get("id", ""),
                    title=item.get("title", ""),
                    slug=item.get("slug", ""),
                    description=item.get("description", ""),
                    end_date=item.get("endDate"),
                    active=item.get("active", True),
                    markets=[m.get("id", "") for m in item.get("markets", [])],
                    tags=[t.get("slug", "") for t in item.get("tags", [])],
                )
                events.append(event)

            self._set_cached(cache_key, [e.to_dict() for e in events])
            return events

        except Exception as e:
            self.logger.warning(f"Failed to get events: {e}")
            return []

    def get_markets_by_event(self, event_id: str) -> list[PolymarketMarket]:
        """
        Get all markets for a specific event.

        Args:
            event_id: Polymarket event ID

        Returns:
            List of PolymarketMarket objects
        """
        if not self.is_enabled:
            return []

        cache_key = f"event_markets:{event_id}"
        cached = self._get_cached(cache_key)
        if cached:
            return [PolymarketMarket.from_dict(m) for m in cached]

        try:
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/events/{event_id}",
            )

            markets = []
            for item in response.get("markets", []):
                market = self._parse_market(item, event_id)
                if market:
                    markets.append(market)

            self._set_cached(cache_key, [m.to_dict() for m in markets])
            return markets

        except Exception as e:
            self.logger.warning(f"Failed to get markets for event {event_id}: {e}")
            return []

    def search_markets_by_keyword(
        self,
        keywords: list[str],
        limit: int = 20,
        synonym_map: Optional[dict[str, list[str]]] = None,
    ) -> list[PolymarketMarket]:
        """
        Search markets by keywords (for arbitrage trigger).

        Supports synonym expansion for better matching.

        Args:
            keywords: List of keywords to search
            limit: Max results per keyword
            synonym_map: Optional synonym mapping for keyword expansion

        Returns:
            List of matching PolymarketMarket objects
        """
        if not self.is_enabled:
            return []

        # Expand keywords using synonym map
        expanded_keywords = self._expand_keywords(keywords, synonym_map)
        self.logger.debug(f"Expanded {len(keywords)} keywords to {len(expanded_keywords)}")

        all_markets = []
        seen_ids = set()

        try:
            # Fetch markets once (more efficient)
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/markets",
                params={
                    "limit": min(limit * len(expanded_keywords), 100),
                    "active": "true",
                },
            )

            for item in (response if isinstance(response, list) else []):
                question = item.get("question", "").lower()
                description = item.get("description", "").lower()
                search_text = f"{question} {description}"

                # Check if any expanded keyword matches
                if self._match_keywords(search_text, expanded_keywords):
                    market_id = item.get("id", "")
                    if market_id and market_id not in seen_ids:
                        market = self._parse_market(item)
                        if market:
                            all_markets.append(market)
                            seen_ids.add(market_id)

        except Exception as e:
            self.logger.warning(f"Failed to search markets: {e}")

        self.logger.info(f"Found {len(all_markets)} markets matching keywords")
        return all_markets

    def _expand_keywords(
        self,
        keywords: list[str],
        synonym_map: Optional[dict[str, list[str]]] = None,
    ) -> set[str]:
        """
        Expand keywords using synonym mapping.

        Args:
            keywords: Original keywords
            synonym_map: Mapping of canonical terms to synonyms

        Returns:
            Expanded set of keywords (all lowercase)
        """
        expanded = set()

        for kw in keywords:
            kw_lower = kw.lower().strip()
            if not kw_lower:
                continue

            # Add original keyword
            expanded.add(kw_lower)

            # Check if keyword matches any synonym group
            if synonym_map:
                for canonical, synonyms in synonym_map.items():
                    synonyms_lower = [s.lower() for s in synonyms]
                    # If keyword is in this synonym group, add all synonyms
                    if kw_lower in synonyms_lower or kw_lower == canonical.lower():
                        expanded.update(synonyms_lower)

        return expanded

    def _match_keywords(
        self,
        text: str,
        keywords: set[str],
    ) -> bool:
        """
        Check if text matches any of the keywords.

        Uses word boundary matching for better accuracy.

        Args:
            text: Text to search in (should be lowercase)
            keywords: Set of keywords to match (all lowercase)

        Returns:
            True if any keyword matches
        """
        for keyword in keywords:
            # Simple substring match for multi-word keywords
            if " " in keyword:
                if keyword in text:
                    return True
            else:
                # Word boundary match for single words
                # This prevents "chip" matching "microchip" unless intended
                import re
                pattern = rf'\b{re.escape(keyword)}\b'
                if re.search(pattern, text):
                    return True

        return False

    def _parse_market(
        self,
        item: dict,
        event_id: Optional[str] = None,
    ) -> Optional[PolymarketMarket]:
        """Parse market data from Gamma API response."""
        try:
            market_id = item.get("id", "")
            if not market_id:
                return None

            # Parse end time and calculate tenor
            end_time_str = item.get("endDate") or item.get("end_date_iso")
            tenor_days = 0
            if end_time_str:
                try:
                    end_dt = datetime.fromisoformat(end_time_str.replace("Z", "+00:00"))
                    now = datetime.now(timezone.utc)
                    tenor_days = max(0, (end_dt - now).days)
                except Exception:
                    pass

            # Get CLOB token IDs
            clob_tokens = item.get("clobTokenIds", [])
            yes_token = clob_tokens[0] if len(clob_tokens) > 0 else None
            no_token = clob_tokens[1] if len(clob_tokens) > 1 else None

            return PolymarketMarket(
                market_id=market_id,
                event_id=event_id or item.get("eventId", item.get("event_id", "")),
                question=item.get("question", ""),
                outcomes=item.get("outcomes", ["Yes", "No"]),
                end_time=end_time_str,
                active=item.get("active", True),
                yes_token_id=yes_token,
                no_token_id=no_token,
                condition_id=item.get("conditionId"),
                tags=[t.get("slug", "") if isinstance(t, dict) else t for t in item.get("tags", [])],
                volume=float(item.get("volume", 0) or 0),
                liquidity=float(item.get("liquidity", 0) or 0),
                tenor_days=tenor_days,
            )
        except Exception as e:
            self.logger.warning(f"Failed to parse market: {e}")
            return None

    def get_orderbook(
        self,
        token_id: str,
    ) -> Optional[dict]:
        """
        Get orderbook from CLOB API.

        Args:
            token_id: CLOB token ID (YES or NO token)

        Returns:
            Orderbook dict with bids/asks or None
        """
        if not self.is_enabled:
            return None

        cache_key = f"orderbook:{token_id}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            response = self._make_request(
                "get",
                f"{self.CLOB_BASE_URL}/book",
                params={"token_id": token_id},
            )

            if response:
                # Short TTL for orderbook (5 seconds)
                self.cache.set(f"{self.name}:{cache_key}", response, ttl=5)
            return response

        except Exception as e:
            self.logger.warning(f"Failed to get orderbook for {token_id}: {e}")
            return None

    def get_quote(self, market: PolymarketMarket) -> Optional[PolymarketQuote]:
        """
        Get quote (bid/ask/mid/depth) for a market.

        Uses YES token orderbook to derive quote.

        Args:
            market: PolymarketMarket object

        Returns:
            PolymarketQuote or None
        """
        if not self.is_enabled or not market.yes_token_id:
            return None

        book = self.get_orderbook(market.yes_token_id)
        if not book:
            return None

        try:
            quote = PolymarketQuote.from_orderbook(
                market_id=market.market_id,
                book=book,
                timestamp=format_timestamp(now_utc()),
            )
            quote.volume_24h = market.volume
            return quote
        except Exception as e:
            self.logger.warning(f"Failed to create quote for {market.market_id}: {e}")
            return None

    def get_quotes_batch(
        self,
        markets: list[PolymarketMarket],
    ) -> dict[str, PolymarketQuote]:
        """
        Get quotes for multiple markets.

        Args:
            markets: List of PolymarketMarket objects

        Returns:
            Dict mapping market_id -> PolymarketQuote
        """
        quotes = {}
        for market in markets:
            quote = self.get_quote(market)
            if quote:
                quotes[market.market_id] = quote
        return quotes

    def get_markets_for_arb(
        self,
        keywords: list[str],
        min_volume: float = 1000,
        min_markets_per_event: int = 2,
        synonym_map: Optional[dict[str, list[str]]] = None,
    ) -> dict[str, list[PolymarketMarket]]:
        """
        Get markets grouped by event for arbitrage detection.

        Only returns events with multiple markets (required for term structure).

        Args:
            keywords: Keywords to search
            min_volume: Minimum volume filter
            min_markets_per_event: Minimum markets in event
            synonym_map: Optional synonym mapping for keyword expansion

        Returns:
            Dict mapping event_id -> list of markets
        """
        if not self.is_enabled:
            return {}

        # Search for markets with synonym expansion
        all_markets = self.search_markets_by_keyword(keywords, synonym_map=synonym_map)

        # Group by event_id
        by_event: dict[str, list[PolymarketMarket]] = {}
        for market in all_markets:
            if market.volume >= min_volume and market.event_id:
                if market.event_id not in by_event:
                    by_event[market.event_id] = []
                by_event[market.event_id].append(market)

        # Filter events with enough markets
        return {
            event_id: markets
            for event_id, markets in by_event.items()
            if len(markets) >= min_markets_per_event
        }
