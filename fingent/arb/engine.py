"""
Arbitrage Engine.

Main orchestrator for Polymarket arbitrage detection.

Flow:
1. Trigger: News keyword match or manual scan
2. Recall: Find relevant Polymarket events/markets
3. Snapshot: Record initial prices (P0)
4. Monitor: Poll quotes periodically
5. Detect: Run term structure strategy
6. Filter: Apply risk controls
7. Notify: Alert via Telegram (optional)
8. Store: Save to persistence layer
"""

import re
from datetime import datetime, timezone
from typing import Any, Optional

from fingent.core.config import get_settings, load_yaml_config
from fingent.core.logging import LoggerMixin
from fingent.core.timeutil import format_timestamp, now_utc
from fingent.domain.models import (
    PolymarketMarket,
    PolymarketQuote,
    ArbSnapshot,
    ArbOpportunity,
)
from fingent.providers.polymarket import PolymarketProvider
from fingent.providers.finnhub import FinnhubProvider
from fingent.arb.strategy import TermStructureStrategy
from fingent.arb.risk import RiskManager


class ArbEngine(LoggerMixin):
    """
    Polymarket Arbitrage Detection Engine.

    Coordinates the full arbitrage detection pipeline.
    """

    def __init__(
        self,
        provider: Optional[PolymarketProvider] = None,
        config: Optional[dict] = None,
    ):
        """
        Initialize arbitrage engine.

        Args:
            provider: Polymarket provider instance
            config: Configuration dict (or load from yaml)
        """
        self.settings = get_settings()

        # Load config
        if config is None:
            full_config = load_yaml_config()
            config = full_config.get("arbitrage", {})
        self.config = config

        # Check if enabled
        self.enabled = config.get("enabled", False)
        if not self.enabled:
            self.logger.warning("Arbitrage engine is disabled in config")

        # Compile keyword patterns
        self.keyword_patterns = []
        for pattern in config.get("trigger_keywords", []):
            try:
                self.keyword_patterns.append(re.compile(pattern, re.IGNORECASE))
            except re.error as e:
                self.logger.warning(f"Invalid regex pattern '{pattern}': {e}")

        # Initialize provider
        self.provider = provider or PolymarketProvider()

        # Initialize strategy and risk manager
        self.strategy = TermStructureStrategy(config)
        self.risk_manager = RiskManager(config.get("risk", {}))

        # In-memory snapshot store (can be moved to Redis/SQLite later)
        self._snapshots: dict[str, ArbSnapshot] = {}

        # Track detected opportunities
        self._opportunities: list[ArbOpportunity] = []

    def check_news_trigger(self, headline: str, summary: str = "") -> list[str]:
        """
        Check if news matches trigger keywords.

        Args:
            headline: News headline
            summary: News summary (optional)

        Returns:
            List of matched keywords
        """
        text = f"{headline} {summary}"
        matched = []

        for pattern in self.keyword_patterns:
            match = pattern.search(text)
            if match:
                matched.append(match.group())

        return matched

    def scan_markets(
        self,
        keywords: Optional[list[str]] = None,
    ) -> dict[str, list[PolymarketMarket]]:
        """
        Scan Polymarket for relevant markets.

        Args:
            keywords: Keywords to search (uses config if not provided)

        Returns:
            Dict mapping event_id -> list of markets
        """
        if not self.enabled or not self.provider.is_enabled:
            return {}

        if keywords is None:
            # Extract keywords from patterns
            keywords = []
            for pattern in self.keyword_patterns:
                # Simple extraction: use pattern string as keyword
                # In production, maintain a separate keyword list
                raw = pattern.pattern.replace("(", "").replace(")", "").replace("|", " ")
                keywords.extend(raw.split())

        # Dedupe and clean
        keywords = list(set(k.strip() for k in keywords if len(k) > 2))[:20]

        self.logger.info(f"Scanning markets for keywords: {keywords[:5]}...")

        risk_config = self.config.get("risk", {})
        min_volume = risk_config.get("min_volume_24h", 5000)

        return self.provider.get_markets_for_arb(
            keywords=keywords,
            min_volume=min_volume,
            min_markets_per_event=2,
        )

    def create_snapshots(
        self,
        markets: list[PolymarketMarket],
        news_id: str = "manual",
    ) -> dict[str, ArbSnapshot]:
        """
        Create initial snapshots for markets.

        Only creates snapshot if not already exists.

        Args:
            markets: List of markets to snapshot
            news_id: Identifier for triggering news

        Returns:
            Dict of market_id -> ArbSnapshot
        """
        snapshots = {}
        timestamp = format_timestamp(now_utc())

        for market in markets:
            if market.market_id in self._snapshots:
                # Already have snapshot
                snapshots[market.market_id] = self._snapshots[market.market_id]
                continue

            # Get current quote
            quote = self.provider.get_quote(market)
            if not quote:
                continue

            snapshot = ArbSnapshot(
                market_id=market.market_id,
                news_id=news_id,
                first_seen_ts=timestamp,
                p0=quote.mid,
                quote0=quote.to_dict(),
                volume0=quote.volume_24h,
            )

            self._snapshots[market.market_id] = snapshot
            snapshots[market.market_id] = snapshot

            self.logger.debug(
                f"Created snapshot for {market.market_id}: p0={quote.mid:.4f}"
            )

        return snapshots

    def detect_opportunities(
        self,
        event_markets: dict[str, list[PolymarketMarket]],
        trigger_ts: Optional[datetime] = None,
    ) -> list[ArbOpportunity]:
        """
        Detect arbitrage opportunities across events.

        Args:
            event_markets: Dict of event_id -> markets
            trigger_ts: Timestamp of triggering event

        Returns:
            List of detected opportunities (before risk filter)
        """
        if not self.enabled:
            return []

        opportunities = []

        for event_id, markets in event_markets.items():
            # Ensure snapshots exist
            snapshots = self.create_snapshots(markets)

            # Get current quotes
            quotes = self.provider.get_quotes_batch(markets)

            if len(quotes) < 2:
                self.logger.debug(f"Event {event_id}: Not enough quotes ({len(quotes)})")
                continue

            # Get snapshots for this event's markets
            event_snapshots = {
                m.market_id: self._snapshots[m.market_id]
                for m in markets
                if m.market_id in self._snapshots
            }

            # Run strategy
            opportunity = self.strategy.evaluate(
                event_id=event_id,
                markets=markets,
                quotes=quotes,
                snapshots=event_snapshots,
                trigger_ts=trigger_ts,
            )

            if opportunity:
                opportunities.append(opportunity)

        return opportunities

    def filter_opportunities(
        self,
        opportunities: list[ArbOpportunity],
        event_markets: dict[str, list[PolymarketMarket]],
    ) -> list[ArbOpportunity]:
        """
        Apply risk filters to opportunities.

        Args:
            opportunities: Raw opportunities from detection
            event_markets: Market data for context

        Returns:
            Filtered opportunities (only CANDIDATE status)
        """
        filtered = []

        for opp in opportunities:
            # Get markets and quotes for this event
            markets = event_markets.get(opp.event_id, [])
            markets_dict = {m.market_id: m for m in markets}
            quotes = self.provider.get_quotes_batch(markets)

            # Apply risk filter
            opp = self.risk_manager.filter(opp, quotes, markets_dict)

            if opp.status == "CANDIDATE":
                opp.status = "CONFIRMED"
                filtered.append(opp)
                self._opportunities.append(opp)

        return filtered

    def run_scan(
        self,
        keywords: Optional[list[str]] = None,
        trigger_ts: Optional[datetime] = None,
    ) -> list[ArbOpportunity]:
        """
        Run a full arbitrage scan.

        Args:
            keywords: Keywords to search (optional)
            trigger_ts: Trigger timestamp (optional)

        Returns:
            List of confirmed opportunities
        """
        if not self.enabled:
            self.logger.warning("Arbitrage engine is disabled")
            return []

        self.logger.info("Starting arbitrage scan...")

        # 1. Scan markets
        event_markets = self.scan_markets(keywords)
        self.logger.info(f"Found {len(event_markets)} events with multiple markets")

        if not event_markets:
            return []

        # 2. Detect opportunities
        raw_opportunities = self.detect_opportunities(event_markets, trigger_ts)
        self.logger.info(f"Detected {len(raw_opportunities)} raw opportunities")

        if not raw_opportunities:
            return []

        # 3. Filter
        confirmed = self.filter_opportunities(raw_opportunities, event_markets)
        self.logger.info(f"Confirmed {len(confirmed)} opportunities after risk filter")

        return confirmed

    def process_news(
        self,
        headline: str,
        summary: str = "",
        news_id: str = "",
    ) -> list[ArbOpportunity]:
        """
        Process a news event and check for arbitrage.

        Args:
            headline: News headline
            summary: News summary
            news_id: Unique news identifier

        Returns:
            List of confirmed opportunities
        """
        if not self.enabled:
            return []

        # Check trigger
        matched_keywords = self.check_news_trigger(headline, summary)

        if not matched_keywords:
            self.logger.debug(f"No keyword match for: {headline[:50]}...")
            return []

        self.logger.info(
            f"News triggered: '{headline[:50]}...' "
            f"(matched: {matched_keywords[:3]})"
        )

        # Run scan with matched keywords
        return self.run_scan(
            keywords=matched_keywords,
            trigger_ts=datetime.now(timezone.utc),
        )

    def get_opportunities(self) -> list[dict]:
        """
        Get all detected opportunities.

        Returns:
            List of opportunity dicts
        """
        return [o.to_dict() for o in self._opportunities]

    def get_snapshots(self) -> dict[str, dict]:
        """
        Get all snapshots.

        Returns:
            Dict of market_id -> snapshot dict
        """
        return {k: v.to_dict() for k, v in self._snapshots.items()}

    def clear_snapshots(self, older_than_hours: float = 6) -> int:
        """
        Clear old snapshots.

        Args:
            older_than_hours: Remove snapshots older than this

        Returns:
            Number of snapshots removed
        """
        now = datetime.now(timezone.utc)
        to_remove = []

        for market_id, snapshot in self._snapshots.items():
            try:
                ts = datetime.fromisoformat(snapshot.first_seen_ts.replace("Z", "+00:00"))
                age_hours = (now - ts).total_seconds() / 3600
                if age_hours > older_than_hours:
                    to_remove.append(market_id)
            except Exception:
                pass

        for market_id in to_remove:
            del self._snapshots[market_id]

        if to_remove:
            self.logger.info(f"Cleared {len(to_remove)} old snapshots")

        return len(to_remove)

    # ==============================================
    # Finnhub News Integration
    # ==============================================

    def scan_finnhub_news(
        self,
        finnhub_provider: Optional[FinnhubProvider] = None,
        category: str = "general",
    ) -> list[ArbOpportunity]:
        """
        Scan Finnhub news and trigger arbitrage detection.

        This is the main entry point for news-triggered arbitrage.

        Args:
            finnhub_provider: Finnhub provider instance (creates one if not provided)
            category: News category (general, forex, crypto, merger)

        Returns:
            List of confirmed opportunities across all triggered news
        """
        if not self.enabled:
            self.logger.warning("Arbitrage engine is disabled")
            return []

        # Initialize Finnhub provider
        if finnhub_provider is None:
            try:
                finnhub_provider = FinnhubProvider()
            except Exception as e:
                self.logger.error(f"Failed to initialize Finnhub provider: {e}")
                return []

        # Fetch news
        self.logger.info(f"Fetching {category} news from Finnhub...")
        news_items = finnhub_provider.get_market_news(category)

        if not news_items:
            self.logger.info("No news items found")
            return []

        self.logger.info(f"Processing {len(news_items)} news items...")

        all_opportunities = []
        triggered_count = 0

        for item in news_items:
            opportunities = self.process_news(
                headline=item.title,
                summary=item.summary,
                news_id=item.url or item.title[:50],
            )

            if opportunities:
                triggered_count += 1
                all_opportunities.extend(opportunities)

        self.logger.info(
            f"News scan complete: {triggered_count} triggered, "
            f"{len(all_opportunities)} opportunities found"
        )

        return all_opportunities

    def run_full_pipeline(
        self,
        use_finnhub: bool = True,
        finnhub_category: str = "general",
    ) -> dict[str, Any]:
        """
        Run the full arbitrage pipeline.

        1. Fetch news from Finnhub (optional)
        2. Check for keyword triggers
        3. Scan Polymarket for matching events
        4. Detect term structure opportunities
        5. Apply risk filters
        6. Return results

        Args:
            use_finnhub: Whether to use Finnhub for news trigger
            finnhub_category: News category if using Finnhub

        Returns:
            Pipeline result dict with stats and opportunities
        """
        result = {
            "timestamp": format_timestamp(now_utc()),
            "enabled": self.enabled,
            "news_scanned": 0,
            "news_triggered": 0,
            "events_found": 0,
            "opportunities_raw": 0,
            "opportunities_confirmed": 0,
            "opportunities": [],
            "errors": [],
        }

        if not self.enabled:
            result["errors"].append("Arbitrage engine is disabled")
            return result

        try:
            if use_finnhub:
                # News-triggered scan
                finnhub = FinnhubProvider()
                news_items = finnhub.get_market_news(finnhub_category)
                result["news_scanned"] = len(news_items)

                for item in news_items:
                    matched = self.check_news_trigger(item.title, item.summary)
                    if matched:
                        result["news_triggered"] += 1

                opportunities = self.scan_finnhub_news(finnhub, finnhub_category)
            else:
                # Manual scan without news trigger
                opportunities = self.run_scan()

            result["opportunities_confirmed"] = len(opportunities)
            result["opportunities"] = [o.to_dict() for o in opportunities]

        except Exception as e:
            self.logger.error(f"Pipeline error: {e}")
            result["errors"].append(str(e))

        return result
