"""
Polymarket provider for prediction market data.

This is an OPTIONAL provider that gracefully degrades if unavailable.
Used for sentiment indicators like Fed rate probabilities.

Supports:
- Gamma API: Market/Event metadata, search
- CLOB API: Orderbook, quotes for arbitrage detection
"""

import json
import re
import time
from datetime import datetime, timezone
from typing import Any, Optional

import httpx

from fingent.core.errors import DataNotAvailableError, ProviderError
from fingent.core.cache import CacheManager
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
        # Short-lived cache for orderbooks
        self._orderbook_cache = CacheManager(maxsize=200, ttl=5)

        # Check if enabled in settings
        if not self.settings.polymarket_enabled:
            self._enabled = False

    def _initialize(self) -> None:
        """Initialize Polymarket client."""
        if not self.settings.polymarket_api_key:
            self.logger.warning(
                "Polymarket API key not configured; using public Gamma/CLOB endpoints"
            )
            self._enabled = True
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
        Search for markets by tag slug/keyword.

        Args:
            query: Tag slug or keyword
            limit: Max results

        Returns:
            List of market dicts
        """
        if not self.is_enabled:
            return []

        tag_id = None
        query_norm = (query or "").strip().lower()
        if query_norm.isdigit():
            tag_id = query_norm
        elif query_norm:
            tag_ids = self.resolve_tag_ids([query_norm])
            tag_id = tag_ids[0] if tag_ids else None

        params = {"limit": limit, "closed": "false"}
        if tag_id:
            params["tag_id"] = tag_id
        elif query_norm:
            params["tag_slug"] = query_norm

        try:
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/markets",
                params=params,
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

    def get_tags(
        self,
        limit: int = 200,
        offset: Optional[int] = None,
        order: Optional[str] = None,
        ascending: Optional[bool] = None,
    ) -> list[dict[str, Any]]:
        """Fetch tags from Gamma API."""
        if not self.is_enabled:
            return []

        cache_key = f"tags:{limit}:{offset or 0}:{order or ''}:{ascending}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        params: dict[str, Any] = {"limit": limit}
        if offset is not None:
            params["offset"] = offset
        if order:
            params["order"] = order
        if ascending is not None:
            params["ascending"] = str(ascending).lower()

        try:
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/tags",
                params=params,
            )
            tags = response if isinstance(response, list) else []
            self._set_cached(cache_key, tags)
            return tags
        except Exception as e:
            self.logger.warning(f"Failed to get tags: {e}")
            return []

    def resolve_tag_ids(
        self,
        tag_inputs: list[str],
        limit: int = 300,
    ) -> list[str]:
        """Resolve tag ids from slugs/labels/keywords."""
        if not tag_inputs:
            return []

        resolved: list[str] = []
        seen = set()

        for raw in tag_inputs:
            if raw and raw.strip().isdigit():
                resolved.append(raw.strip())
                seen.add(raw.strip())

        for raw in tag_inputs:
            slug = self._normalize_tag_string(raw)
            if not slug or slug.isdigit():
                continue
            tag = self.get_tag_by_slug(slug)
            if not tag:
                continue
            tag_id = str(tag.get("id", "") or "").strip()
            if tag_id and tag_id not in seen:
                resolved.append(tag_id)
                seen.add(tag_id)

        tags = self.get_tags(limit=limit)
        if not tags:
            return resolved

        normalized_inputs = [self._normalize_tag_string(t) for t in tag_inputs if t]

        for tag in tags:
            tag_id = str(tag.get("id", "") or "").strip()
            if not tag_id or tag_id in seen:
                continue
            slug = self._normalize_tag_string(tag.get("slug"))
            label = self._normalize_tag_string(tag.get("label") or tag.get("name"))

            for needle in normalized_inputs:
                if not needle:
                    continue
                if needle == slug or needle == label:
                    resolved.append(tag_id)
                    seen.add(tag_id)
                    break
                if needle in slug or needle in label:
                    resolved.append(tag_id)
                    seen.add(tag_id)
                    break

        return resolved

    @staticmethod
    def _normalize_tag_string(value: Optional[str]) -> str:
        return re.sub(r"\s+", " ", (value or "").strip().lower())

    def get_tag_by_slug(self, slug: str) -> Optional[dict[str, Any]]:
        """Fetch a tag by slug via Gamma API."""
        if not self.is_enabled:
            return None

        slug_norm = self._normalize_tag_string(slug)
        if not slug_norm:
            return None

        cache_key = f"tag_slug:{slug_norm}"
        cached = self._get_cached(cache_key)
        if cached:
            return cached

        try:
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/tags/slug/{slug_norm}",
            )
            if isinstance(response, dict):
                self._set_cached(cache_key, response)
                return response
        except Exception as e:
            self.logger.warning(f"Failed to get tag by slug '{slug_norm}': {e}")
        return None

    def get_events(
        self,
        tag: Optional[str] = None,
        tag_id: Optional[str] = None,
        active: Optional[bool] = True,
        closed: Optional[bool] = False,
        limit: int = 50,
        offset: Optional[int] = None,
        order: Optional[str] = None,
        ascending: Optional[bool] = None,
    ) -> list[PolymarketEvent]:
        """
        Get events from Gamma API.

        Args:
            tag: Tag slug/keyword (deprecated, resolves to tag_id)
            tag_id: Tag id
            active: Filter active events (optional)
            closed: Filter closed events (False for open events only)
            limit: Max results
            offset: Pagination offset
            order: Sort field
            ascending: Sort direction

        Returns:
            List of PolymarketEvent objects
        """
        if not self.is_enabled:
            return []

        resolved_tag_id = tag_id
        if not resolved_tag_id and tag:
            tag_ids = self.resolve_tag_ids([tag])
            resolved_tag_id = tag_ids[0] if tag_ids else None

        cache_key = (
            f"events:{resolved_tag_id or tag or 'all'}:"
            f"{active}:{closed}:{limit}:{offset or 0}:{order or ''}:{ascending}"
        )
        cached = self._get_cached(cache_key)
        if cached:
            return [PolymarketEvent.from_dict(e) for e in cached]

        try:
            params: dict[str, Any] = {"limit": limit}
            if resolved_tag_id:
                params["tag_id"] = resolved_tag_id
            if active is not None:
                params["active"] = str(active).lower()
            if closed is not None:
                params["closed"] = str(closed).lower()
            if order:
                params["order"] = order
            if ascending is not None:
                params["ascending"] = str(ascending).lower()
            if offset is not None:
                params["offset"] = offset

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
                    category=item.get("category"),
                    end_date=item.get("endDate") or item.get("end_date"),
                    active=item.get("active", True),
                    markets=[m.get("id", "") for m in item.get("markets", [])],
                    markets_data=item.get("markets", []) or [],
                    tags=[t.get("slug", "") for t in item.get("tags", [])],
                )
                events.append(event)

            self._set_cached(cache_key, [e.to_dict() for e in events])
            return events

        except Exception as e:
            self.logger.warning(f"Failed to get events: {e}")
            return []

    def get_markets_by_event(
        self,
        event_id: str,
        event_slug: Optional[str] = None,
    ) -> list[PolymarketMarket]:
        """
        Get all markets for a specific event.

        Args:
            event_id: Polymarket event ID
            event_slug: Polymarket event slug (preferred when available)

        Returns:
            List of PolymarketMarket objects
        """
        if not self.is_enabled:
            return []

        cache_key = f"event_markets:{event_id}:{event_slug or ''}"
        cached = self._get_cached(cache_key)
        if cached:
            return [PolymarketMarket.from_dict(m) for m in cached]

        try:
            response = None
            if event_slug:
                response = self._make_request(
                    "get",
                    f"{self.BASE_URL}/events/slug/{event_slug}",
                )
            if response is None:
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
        use_clob: bool = True,
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

        if use_clob:
            return self._search_clob_markets(expanded_keywords, limit=limit)

        all_markets = []
        seen_ids = set()

        try:
            # Fetch markets once (more efficient)
            response = self._make_request(
                "get",
                f"{self.BASE_URL}/markets",
                params={
                    "limit": min(limit * len(expanded_keywords), 200),
                    "active": "true",
                    "closed": "false",
                    "archived": "false",
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
                        if market and self._is_market_usable(market):
                            all_markets.append(market)
                            seen_ids.add(market_id)

        except Exception as e:
            self.logger.warning(f"Failed to search markets: {e}")

        self.logger.info(f"Found {len(all_markets)} markets matching keywords")
        return all_markets

    def _search_clob_markets(
        self,
        expanded_keywords: set[str],
        limit: int = 20,
        page_size: int = 200,
        max_pages: int = 5,
    ) -> list[PolymarketMarket]:
        """Search active CLOB markets by keyword for fresher results."""
        results: list[PolymarketMarket] = []
        seen_ids = set()
        offset = 0
        pages = 0

        while len(results) < limit and pages < max_pages:
            try:
                response = self._make_request(
                    "get",
                    f"{self.CLOB_BASE_URL}/markets",
                    params={"limit": page_size, "offset": offset},
                )
            except Exception:
                break

            data = response.get("data", []) if isinstance(response, dict) else []
            if not data:
                break

            for item in data:
                if not item.get("active", False):
                    continue
                if item.get("archived", False):
                    continue
                if item.get("closed", False):
                    continue
                if not item.get("enable_order_book", False):
                    continue
                if not item.get("accepting_orders", False):
                    continue

                question = item.get("question", "")
                description = item.get("description", "")
                tags = item.get("tags", [])
                tag_text = " ".join([t.get("slug", "") if isinstance(t, dict) else str(t) for t in tags])
                search_text = f"{question} {description} {tag_text}".lower()

                if not self._match_keywords(search_text, expanded_keywords):
                    continue

                market_id = item.get("condition_id") or item.get("question_id") or item.get("market_slug")
                if not market_id or market_id in seen_ids:
                    continue

                tokens = item.get("tokens", [])
                yes_token = tokens[0].get("token_id") if len(tokens) > 0 else None
                no_token = tokens[1].get("token_id") if len(tokens) > 1 else None

                market = PolymarketMarket(
                    market_id=str(market_id),
                    event_id=str(item.get("question_id") or market_id),
                    question=question,
                    outcomes=[t.get("outcome", "") for t in tokens] if tokens else ["Yes", "No"],
                    end_time=item.get("end_date_iso"),
                    active=item.get("active", True),
                    yes_token_id=yes_token,
                    no_token_id=no_token,
                    condition_id=item.get("condition_id"),
                    tags=[t.get("slug", "") if isinstance(t, dict) else str(t) for t in tags],
                    volume=0.0,
                    liquidity=0.0,
                )

                results.append(market)
                seen_ids.add(market_id)
                if len(results) >= limit:
                    break

            offset += page_size
            pages += 1

        self.logger.info(f"Found {len(results)} CLOB markets matching keywords")
        return results

    @staticmethod
    def _is_market_usable(market: PolymarketMarket) -> bool:
        """Filter out expired/inactive or non-CLOB markets."""
        if not market.active:
            return False
        # Require CLOB token for quote
        if not market.yes_token_id:
            return False
        # Filter expired markets if end_time is present
        if market.end_time:
            try:
                end_dt = datetime.fromisoformat(market.end_time.replace("Z", "+00:00"))
                if end_dt <= datetime.now(timezone.utc):
                    return False
            except Exception:
                pass
        return True

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

            # Get CLOB token IDs (normalize to list)
            clob_tokens = self._normalize_clob_tokens(item.get("clobTokenIds", []))
            yes_token = clob_tokens[0] if len(clob_tokens) > 0 else None
            no_token = clob_tokens[1] if len(clob_tokens) > 1 else None

            return PolymarketMarket(
                market_id=market_id,
                event_id=event_id or item.get("eventId", item.get("event_id", "")),
                question=item.get("question", ""),
                outcomes=self._normalize_outcomes(item.get("outcomes")),
                end_time=end_time_str,
                active=item.get("active", True),
                yes_token_id=yes_token,
                no_token_id=no_token,
                condition_id=item.get("conditionId"),
                tags=[t.get("slug", "") if isinstance(t, dict) else t for t in item.get("tags", [])],
                volume=float(item.get("volume", 0) or 0),
                liquidity=float(item.get("liquidity", 0) or 0),
                outcome_prices=self._normalize_prices(item.get("outcomePrices")),
                tenor_days=tenor_days,
            )
        except Exception as e:
            self.logger.warning(f"Failed to parse market: {e}")
            return None

    @staticmethod
    def _normalize_clob_tokens(value: Any) -> list[str]:
        """Normalize CLOB token ids to a list of strings."""
        if isinstance(value, list):
            return [str(v) for v in value if v]
        if isinstance(value, str):
            # Some APIs return JSON string like '["id1","id2"]'
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed if v]
            except Exception:
                return []
        return []

    @staticmethod
    def _normalize_outcomes(value: Any) -> list[str]:
        if isinstance(value, list):
            return [str(v) for v in value if v]
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [str(v) for v in parsed if v]
            except Exception:
                return []
        return ["Yes", "No"]

    @staticmethod
    def _normalize_prices(value: Any) -> list[float]:
        if isinstance(value, list):
            prices = []
            for v in value:
                try:
                    prices.append(float(v))
                except Exception:
                    continue
            return prices
        if isinstance(value, str):
            try:
                parsed = json.loads(value)
                if isinstance(parsed, list):
                    return [float(v) for v in parsed if v is not None]
            except Exception:
                return []
        return []

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
        if not self.is_enabled or not token_id:
            return None

        # Some markets don't have orderbooks; avoid noisy errors for invalid tokens
        if isinstance(token_id, str) and token_id.strip() in {"", "[]", "None"}:
            return None

        cache_key = f"orderbook:{token_id}"
        cached = self._orderbook_cache.get(cache_key)
        if cached:
            return cached

        try:
            response = self.http.client.get(
                f"{self.CLOB_BASE_URL}/book",
                params={"token_id": token_id},
                timeout=self.http.timeout,
            )

            if response.status_code == 404:
                # No orderbook for this token; treat as missing
                return None

            if response.status_code >= 400:
                self.logger.warning(
                    f"Orderbook request failed: HTTP {response.status_code}"
                )
                return None

            data = response.json()
            if data:
                # Cache orderbook with short TTL
                self._orderbook_cache.set(cache_key, data)
            return data

        except (httpx.TimeoutException, httpx.NetworkError) as e:
            self.logger.warning(f"Orderbook request error: {e}")
            return None
        except Exception as e:
            self.logger.warning(f"Failed to get orderbook for {token_id}: {e}")
        return None

    def get_price(self, token_id: str, side: str = "buy") -> Optional[float]:
        """Get price from CLOB price endpoint (fallback when orderbook missing)."""
        if not self.is_enabled or not token_id:
            return None

        try:
            response = self.http.client.get(
                f"{self.CLOB_BASE_URL}/price",
                params={"token_id": token_id, "side": side},
                timeout=self.http.timeout,
            )
            if response.status_code >= 400:
                return None
            data = response.json()
            if isinstance(data, dict) and "price" in data:
                return float(data.get("price"))
            if isinstance(data, (int, float)):
                return float(data)
        except Exception as e:
            self.logger.warning(f"Price request error: {e}")
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
        if not self.is_enabled:
            return None

        if market.yes_token_id:
            book = self.get_orderbook(market.yes_token_id)
            # Only use orderbook if it has actual bids or asks
            if book and (book.get("bids") or book.get("asks")):
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

            # Fallback to price endpoint when orderbook missing
            price = self.get_price(market.yes_token_id, side="buy")
            if price is not None:
                return PolymarketQuote(
                    market_id=market.market_id,
                    timestamp=format_timestamp(now_utc()),
                    bid=price,
                    ask=price,
                    mid=price,
                    spread=0.0,
                    spread_bps=0.0,
                    depth_bid=0.0,
                    depth_ask=0.0,
                    volume_24h=market.volume,
                )

        # Fallback to Gamma outcomePrices if available
        if market.outcome_prices:
            try:
                yes_price = float(market.outcome_prices[0])
                return PolymarketQuote(
                    market_id=market.market_id,
                    timestamp=format_timestamp(now_utc()),
                    bid=yes_price,
                    ask=yes_price,
                    mid=yes_price,
                    spread=0.0,
                    spread_bps=0.0,
                    depth_bid=0.0,
                    depth_ask=0.0,
                    volume_24h=market.volume,
                )
            except Exception:
                pass
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
